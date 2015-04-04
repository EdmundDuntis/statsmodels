"""
Mediation analysis

Implements algorithm 1 ('parametric inference') and algorithm 2
('nonparametric inference') from:

Imai, Keele, Tingley (2010).  A general approach to causal mediation
analysis. Psychological Methods 15:4, 309-334.

http://imai.princeton.edu/research/files/BaronKenny.pdf

The algorithms are described on page 317 of the paper.

In the case of linear models with no interactions involving the
mediator, the results should be similar or identical to the earlier
Barron-Kenny approach.
"""

import numpy as np
import pandas as pd
from statsmodels.graphics.utils import maybe_name_or_idx


class Mediation(object):
    """
    Conduct a mediation analysis.

    Parameters
    ----------
    outcome_model : statsmodels model
        Regression model for the outcome.  Predictor variables include
        the treatment/exposure, the mediator, and any other variables
        of interest.
    mediator_model : statsmodels model
        Regression model for the mediator variable.  Predictor
        variables include the treatment/exposure and any other
        variables of interest.
    exposure : string or (int, int) tuple
        The name or column position of the treatment/exposure
        variable.  If positions are given, the first integer is the
        column position of the exposure variable in the outcome model
        and the second integer is the position of the exposure variable
        in the mediator model.  If a string is given, it must be the name
        of the exposure variable in both regression models.
    mediator : string or int
        The name or column position of the mediator variable in the
        outcome regression model.  If None, infer the name from the
        mediator model formula (if present).
    moderators : dict
        Map from variable names or index positions to values of
        moderator variables that are held fixed when calculating
        mediation effects.  If the keys are index position they must
        be tuples `(i, j)` where `i` is the index in the outcome model
        and `j` is the index in the mediator model.  Otherwise the
        keys must be variable names.
    outcome_fit_kwargs : dict-like
        Keyword arguments to use when fitting the outcome model.
    mediator_fit_kwargs : dict-like
        Keyword arguments to use when fitting the mediator model.

    Returns a ``MediationResults`` object.

    Notes
    -----
    The mediator model class must implement ``get_distribution``.

    Examples
    --------
    A basic mediation analysis using formulas:

    >>> probit = sm.families.links.probit
    >>> outcome_model = sm.GLM.from_formula("cong_mesg ~ emo + treat + age + educ + gender + income",
                                        data, family=sm.families.Binomial(link=probit))
    >>> mediator_model = sm.OLS.from_formula("emo ~ treat + age + educ + gender + income", data)
    >>> med = Mediation(outcome_model, mediator_model, "treat", "emo").fit()
    >>> med.summary()

    A basic mediation analysis without formulas.  This may be slightly
    faster than the approach using formulas.  If there are any
    interactions involving the treatment or mediator variables this
    approach will not work, you must use formulas.

    >>> outcome = np.asarray(data["cong_mesg"])
    >>> outcome_exog = patsy.dmatrix("emo + treat + age + educ + gender + income", data,
                                  return_type='dataframe')
    >>> probit = sm.families.links.probit
    >>> outcome_model = sm.GLM(outcome, outcome_exog, family=sm.families.Binomial(link=probit))
    >>> mediator = np.asarray(data["emo"])
    >>> mediator_exog = patsy.dmatrix("treat + age + educ + gender + income", data,
                                 return_type='dataframe')
    >>> mediator_model = sm.OLS(mediator, mediator_exog)
    >>> tx_pos = [outcome_exog.columns.tolist().index("treat"),
    >>>           mediator_exog.columns.tolist().index("treat")]
    >>> med_pos = outcome_exog.columns.tolist().index("emo")
    >>> med = Mediation(outcome_model, mediator_model, tx_pos, med_pos).fit()
    >>> med.summary()

    A moderated mediation analysis.  The mediation effect is computed
    for people of age 20.

    >>> fml = "cong_mesg ~ emo + treat*age + emo*age + educ + gender + income",
    >>> outcome_model = sm.GLM.from_formula(fml, data,
    >>>                                     family=sm.families.Binomial())
    >>> mediator_model = sm.OLS.from_formula("emo ~ treat*age + educ + gender + income", data)
    >>> moderators = {"age" : 20}
    >>> med = Mediation(outcome_model, mediator_model, "treat", "emo",
                        moderators=moderators).fit()

    References
    ----------
    Imai, Keele, Tingley (2010).  A general approach to causal mediation
    analysis. Psychological Methods 15:4, 309-334.
    http://imai.princeton.edu/research/files/BaronKenny.pdf

    Tingley, Yamamoto, Hirose, Keele, Imai (2014).  mediation : R
    package for causal mediation analysis.  Journal of Statistical
    Software 59:5.  http://www.jstatsoft.org/v59/i05/paper
    """

    def __init__(self, outcome_model, mediator_model, exposure, mediator=None,
                 moderators=None, outcome_fit_kwargs=None, mediator_fit_kwargs=None):

        self.outcome_model = outcome_model
        self.mediator_model = mediator_model
        self.exposure = exposure
        self.moderators = moderators if moderators is not None else {}

        if mediator is None:
            self.mediator = self._guess_endog_name(mediator_model, 'mediator')
        else:
            self.mediator = mediator

        self._outcome_fit_kwargs = (outcome_fit_kwargs if outcome_fit_kwargs
                                    is not None else {})
        self._mediator_fit_kwargs = (mediator_fit_kwargs if mediator_fit_kwargs
                                     is not None else {})

        # We will be changing these so need to copy.
        self._outcome_exog = outcome_model.exog.copy()
        self._mediator_exog = mediator_model.exog.copy()

        # Position of the exposure variable in the mediator model.
        self._exp_pos_mediator = self._variable_pos('exposure', 'mediator')

        # Position of the exposure variable in the outcome model.
        self._exp_pos_outcome = self._variable_pos('exposure', 'outcome')

        # Position of the mediator variable in the outcome model.
        self._med_pos_outcome = self._variable_pos('mediator', 'outcome')


    def _variable_pos(self, var, model):
        if model == 'mediator':
            mod = self.mediator_model
        else:
            mod = self.outcome_model

        if var == 'mediator':
            return maybe_name_or_idx(self.mediator, mod)[1]

        exp = self.exposure
        exp_is_2 = ((len(exp) == 2) and (type(exp) != type('')))

        if exp_is_2:
            if model == 'outcome':
                return exp[0]
            elif model == 'mediator':
                return exp[1]
        else:
            return maybe_name_or_idx(exp, mod)[1]


    def _guess_endog_name(self, model, typ):
        if hasattr(model, 'formula'):
            return model.formula.split("~")[0].strip()
        else:
            raise ValueError('cannot infer %s name without formula' % typ)


    def _simulate_params(self, result):
        """
        Simulate model parameters from fitted sampling distribution.
        """
        mn = result.params
        cov = result.cov_params()
        return np.random.multivariate_normal(mn, cov)


    def _get_mediator_exog(self, exposure):
        """
        Return the mediator exog matrix with exposure set to the given
        value.  Set values of moderated variables as needed.
        """
        mediator_exog = self._mediator_exog
        if not hasattr(self.mediator_model, 'formula'):
            mediator_exog[:, self._exp_pos_mediator] = exposure
            for ix in self.moderators:
                v = self.moderators[ix]
                mediator_exog[:, ix[1]] = v
        else:
            # Need to regenerate the model exog
            df = self.mediator_model.data.frame.copy()
            df.loc[:, self.exposure] = exposure
            for vname in self.moderators:
                v = self.moderators[vname]
                df.loc[:, vname] = v
            klass = self.mediator_model.__class__
            init_kwargs = self.mediator_model._get_init_kwds()
            model = klass.from_formula(data=df, **init_kwargs)
            mediator_exog = model.exog

        return mediator_exog


    def _get_outcome_exog(self, exposure, mediator):
        """
        Retun the exog design matrix with mediator and exposure set to
        the given values.  Set values of moderated variables as
        needed.
        """
        outcome_exog = self._outcome_exog
        if not hasattr(self.outcome_model, 'formula'):
            outcome_exog[:, self._med_pos_outcome] = mediator
            outcome_exog[:, self._exp_pos_outcome] = exposure
            for ix in self.moderators:
                v = self.moderators[ix]
                outcome_exog[:, ix[0]] = v
        else:
            # Need to regenerate the model exog
            df = self.outcome_model.data.frame.copy()
            df.loc[:, self.exposure] = exposure
            df.loc[:, self.mediator] = mediator
            for vname in self.moderators:
                v = self.moderators[vname]
                df.loc[:, vname] = v
            klass = self.outcome_model.__class__
            init_kwargs = self.outcome_model._get_init_kwds()
            model = klass.from_formula(data=df, **init_kwargs)
            outcome_exog = model.exog

        return outcome_exog


    def _fit_model(self, model, fit_kwargs, boot=False):
        klass = model.__class__
        init_kwargs = model._get_init_kwds()
        endog = model.endog
        exog = model.exog
        if boot:
            ii = np.random.randint(0, len(endog), len(endog))
            endog = endog[ii]
            exog = exog[ii, :]
        outcome_model = klass(endog, exog, **init_kwargs)
        return model.fit(**fit_kwargs)


    def fit(self, method="parametric", n_rep=1000):

        if method.startswith("para"):
            # Initial fit to unperturbed data.
            outcome_result = self._fit_model(self.outcome_model, self._outcome_fit_kwargs)
            mediator_result = self._fit_model(self.mediator_model, self._mediator_fit_kwargs)
        elif not method.startswith("boot"):
            raise("method must be either 'parametric' or 'bootstrap'")

        indirect_effects = [[], []]
        direct_effects = [[], []]

        for iter in range(n_rep):

            if method == "parametric":
                # Realization of outcome model parameters from sampling distribution
                outcome_params = self._simulate_params(outcome_result)

                # Realization of mediation model parameters from sampling distribution
                mediation_params = self._simulate_params(mediator_result)
            else:
                outcome_result = self._fit_model(self.outcome_model,
                                                 self._outcome_fit_kwargs, boot=True)
                outcome_params = outcome_result.params
                mediator_result = self._fit_model(self.mediator_model,
                                                  self._mediator_fit_kwargs, boot=True)
                mediation_params = mediator_result.params

            # predicted outcomes[tm][te] is the outcome when the
            # mediator is set to tm and the outcome/exposure is set to
            # te.
            predicted_outcomes = [[None, None], [None, None]]
            for tm in 0, 1:
                mex = self._get_mediator_exog(tm)
                gen = self.mediator_model.get_distribution(mediation_params,
                                                           mediator_result.scale,
                                                           exog=mex)
                potential_mediator = gen.rvs(mex.shape[0])

                for te in 0, 1:
                    oex = self._get_outcome_exog(te, potential_mediator)
                    po = self.outcome_model.predict(outcome_params, oex)
                    predicted_outcomes[tm][te] = po

            for t in 0, 1:
                indirect_effects[t].append(predicted_outcomes[1][t] - predicted_outcomes[0][t])
                direct_effects[t].append(predicted_outcomes[t][1] - predicted_outcomes[t][0])

        for t in 0, 1:
            indirect_effects[t] = np.asarray(indirect_effects[t]).T
            direct_effects[t] = np.asarray(direct_effects[t]).T

        self.indirect_effects = indirect_effects
        self.direct_effects = direct_effects

        return MediationResults(self.indirect_effects, self.direct_effects)


class MediationResults(object):

    def __init__(self, indirect_effects, direct_effects):

        self.indirect_effects = indirect_effects
        self.direct_effects = direct_effects

        indirect_effects_avg = [None, None]
        direct_effects_avg = [None, None]
        for t in 0, 1:
            indirect_effects_avg[t] = indirect_effects[t].mean(0)
            direct_effects_avg[t] = direct_effects[t].mean(0)

        self.ACME_ctrl = indirect_effects_avg[0]
        self.ACME_tx = indirect_effects_avg[1]
        self.ADE_ctrl = direct_effects_avg[0]
        self.ADE_tx = direct_effects_avg[1]

        self.total_effect = (self.ACME_ctrl + self.ACME_tx + self.ADE_ctrl + self.ADE_tx) / 2

        self.prop_med_ctrl = self.ACME_ctrl / self.total_effect
        self.prop_med_tx = self.ACME_tx / self.total_effect
        self.prop_med_avg = (self.prop_med_ctrl + self.prop_med_tx) / 2

        self.ACME_avg = (self.ACME_ctrl + self.ACME_tx) / 2
        self.ADE_avg = (self.ADE_ctrl + self.ADE_tx) / 2


    def summary(self):

        columns = ["Estimate", "95% lower bound", "95% upper bound"]
        index = ["ACME (control)", "ACME (treated)", "ADE (control)", "ADE (treated)",
                 "Total effect", "Prop. mediated (control)", "Prop. mediated (treated)",
                 "ACME (average)", "ADE (average)", "Prop. mediated (average)"]
        smry = pd.DataFrame(columns=columns, index=index)

        for i, vec in enumerate([self.ACME_ctrl, self.ACME_tx, self.ADE_tx, self.ADE_ctrl,
                                 self.total_effect, self.prop_med_ctrl,
                                 self.prop_med_tx, self.ACME_avg, self.ADE_avg,
                                 self.prop_med_avg]):

            if ((vec is self.prop_med_ctrl) or (vec is self.prop_med_tx) or
                (vec is self.prop_med_avg)):
                smry.iloc[i, 0] = np.median(vec)
            else:
                smry.iloc[i, 0] = vec.mean()
            smry.iloc[i, 1] = np.percentile(vec, 2.5)
            smry.iloc[i, 2] = np.percentile(vec, 97.5)

        smry = smry.convert_objects(convert_numeric=True)

        return smry

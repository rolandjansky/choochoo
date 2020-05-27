
from collections import namedtuple
from json import loads
from logging import getLogger
from re import split

import numpy as np
import pandas as pd

from .utils import MultiProcCalculator, ActivityGroupCalculatorMixin, DataFrameCalculatorMixin
from ..loader import StatisticJournalLoader
from ...data import present, linear_resample_time, Statistics
from ...data.frame import median_dt
from ...data.lib import interpolate_to_index
from ...data.power import add_differentials, add_energy_budget, add_loss_estimate, add_power_estimate, PowerException, \
    evaluate, fit_power, PowerModel, add_air_speed, add_modeled_hr
from ...lib import log_current_exception
from ...lib.data import reftuple, MissingReference
from ...names import N, Units, Summaries, T
from ...sql import StatisticJournalFloat, Constant, Timestamp
from ...sql.types import simple_name

log = getLogger(__name__)

# these configure the model.
Power = reftuple('Power', 'bike, rider_weight, vary', defaults=(64, ''))
Bike = namedtuple('Bike', 'cda, crr, weight')


# used as common owner
class PowerCalculator(ActivityGroupCalculatorMixin, DataFrameCalculatorMixin, MultiProcCalculator):

    def __init__(self, *args, **kargs):
        super().__init__(*args, owner_out=PowerCalculator, **kargs)
        self.power = None


class BasicPowerCalculator(PowerCalculator):

    '''
    This is configured in the pipeline table with the 'power' parameter set to, for example, 'PowerEstimate.Bike'.
    That is then loaded from constants.  Since this is a 'reftuple' it can refer to other entries in the database.
    Currently the 'bike' attribute of 'power' is defined as '${Constant:Power.${SegmentReader:kit}}'.
    '''

    def __init__(self, *args, power=None, caloric_eff=0.25, **kargs):
        self.power_ref = power
        self.caloric_eff = caloric_eff
        super().__init__(*args, **kargs)

    def _set_power(self, s, ajournal):
        power = Power(**loads(Constant.get(s, self.power_ref).at(s).value))
        # default owner is constant since that's what users can tweak
        self.power = power.expand(s, ajournal.start, default_owner=Constant)
        log.debug(f'Power: {self.power_ref}: {self.power}')

    def _read_dataframe(self, s, ajournal):
        from ..owners import SegmentReader, ElevationCalculator
        try:
            self._set_power(s, ajournal)
            df = Statistics(s, activity_journal=ajournal, with_timespan=True). \
                by_name(SegmentReader, N.DISTANCE, N.SPEED, N.CADENCE, N.LATITUDE,
                        N.LONGITUDE, N.HEART_RATE). \
                by_name(ElevationCalculator, N.ELEVATION).df
            ldf = linear_resample_time(df)
            ldf = add_differentials(ldf, max_gap=1.1 * median_dt(df))
            if N.HEADING not in ldf.columns:
                raise PowerException('Could not calculate heading')    
            return df, ldf
        except PowerException as e:
            log.warning(e)
        except MissingReference as e:
            log.warning(f'Power configuration incorrect ({e})')
        except Exception as e:
            log.warning(f'Failed to generate statistics for power ({ajournal.activity_group.name}): {e}')
            log_current_exception(traceback=True)

    def _calculate_stats(self, s, ajournal, dfs):
        df, ldf = dfs
        weight = self.power.bike['weight'] + self.power.rider_weight
        ldf = add_energy_budget(ldf, weight)
        ldf = add_air_speed(ldf, 0, 0)
        ldf = add_loss_estimate(ldf, weight, cda=self.power.bike['cda'], crr=self.power.bike['crr'])
        ldf = add_power_estimate(ldf)
        return df, ldf

    def _copy_results(self, s, ajournal, loader, dfs,
                      fields=((T.POWER_ESTIMATE, Units.W, Summaries.AVG, 'The estimated power.'),
                              (T.HEADING, Units.DEG, None, 'The current heading'))):
        df, ldf = dfs
        self.__add_total_energy(s, ajournal, loader, ldf)
        df = interpolate_to_index(df, ldf, *(simple_name(field[0]) for field in fields))
        for time, row in df.iterrows():
            for title, units, summary, description in fields:
                name = simple_name(title)
                if not pd.isnull(row[name]):
                    loader.add(name, units, summary, ajournal, row[name], time,
                               StatisticJournalFloat, title=title, description=description)

    def __add_total_energy(self, s, ajournal, loader, ldf):
        if present(ldf, N.POWER_ESTIMATE):
            ldf['tmp'] = ldf[N.POWER_ESTIMATE]
            ldf.loc[ldf['tmp'].isna(), ['tmp']] = 0
            energy = np.trapz(y=ldf['tmp'], x=ldf.index.astype(np.int64) / 1e12)
            loader.add(T.ENERGY_ESTIMATE, Units.KJ, Summaries.MAX, ajournal, energy, ajournal.start,
                       StatisticJournalFloat, 'The estimated total energy expended.')
            loader.add(T.CALORIE_ESTIMATE, Units.KCAL, Summaries.MAX, ajournal,
                       energy * 0.239006 / self.caloric_eff, ajournal.start, StatisticJournalFloat,
                       'The estimated calories burnt.')
            ldf.drop(columns=['tmp'], inplace=True)


class ExtendedPowerCalculator(BasicPowerCalculator):
    '''
    This was an experiment that honestly didn't work too well.

    The idea was to extend the power model with constant speed / heading wind, and to fit for that,
    so that we included a wind correction.  To constrain the fitting I took the power, scaled and lagged,
    and compared it to heart rate.  The best scale/lag and wind model gave the power estimate,

    However, in testing, none of the windiest routes were the windy days cycling back down the Maipo valley.
    Instead, it tended to pick interval training when riding a loop.  Obviously fitting the pattern in the
    activity, not the wind.

    And it increased loading times so much it drove the re-implementation with multiple processes.
    '''

    # lots of fitting
    def __init__(self, *args, cost_calc=100, **kargs):
        super().__init__(*args, cost_calc=cost_calc, **kargs)

    def _run_one(self, s, time_or_date):
        source = self._get_source(s, time_or_date)
        s.commit()  # free up database
        with Timestamp(owner=self.owner_out, source=source).on_success(s):
            try:
                data = self._read_dataframe(s, source)
                if data:
                    loader = StatisticJournalLoader(s, self.owner_out)
                    try:
                        stats = self._calculate_stats(s, source, data)
                    except PowerException as e:
                        log.debug(f'Cannot use detailed power model; adding basic values only ({e})')
                        loader = StatisticJournalLoader(s, self.owner_out)
                        stats = None, super()._calculate_stats(s, source, data)
                    self._copy_results(s, source, loader, stats)
                    loader.load()
            except Exception as e:
                log.error(f'No statistics on {time_or_date}')
                log_current_exception()

    def __varying(self):
        return list(filter(None, split(r'[\s,]*([^, ]+)[\s ]*', self.power.vary)))

    def _calculate_stats(self, s, ajournal, df):
        vary = self.__varying()
        if not vary:
            raise PowerException('No parameters to vary - fitting disabled')
        model = PowerModel(cda=self.power.bike['cda'], crr=self.power.bike['crr'],
                           m=self.power.bike['weight'] + self.power.rider_weight,
                           wind_speed=10, wind_heading=180)
        for name in self.power._fields:
            if name in model._fields:
                setattr(model, name, getattr(self.power, name))
        model = fit_power(df, model, *vary)
        df = evaluate(df, model, quiet=False)
        df = add_modeled_hr(df, model.window, model.slope, model.delay)
        # p_hr = 60 / model.slope
        # if p_hr < 100 or p_hr > 500:
        #     raise PowerException(f'Unreasonable model results (slope {model.slope} / {p_hr})')
        # if model.delay > 30:
        #     raise PowerException(f'Unreasonable model results (delay {model.delay})')
        return model, df

    def _copy_results(self, s, ajournal, loader, stats):
        model, df = stats
        fields = ((T.POWER_ESTIMATE, Units.W, Summaries.AVG, 'The estimated power.'),
                  (T.HEADING, Units.DEG, None, 'The current heading.'))
        if model:
            # how much energy every heart beat
            # 60W at 60bpm is 60J every second or beat; 60W at 1bpm is 3600J every minute or beat;
            # 1W at 1bpm is 60J every minute or beat
            # slope is BPM / W; 1/slope is W/BPM = W/PM = WM = 60Ws
            vary = self.__varying()
            if 'slope' in vary:
                loader.add(T.POWER_HR, Units.J, Summaries.AVG, ajournal,
                           60 / model.slope, ajournal.start, StatisticJournalFloat)
            if 'delay' in vary:
                loader.add(T.POWER_HR_LAG, Units.S, Summaries.AVG, ajournal, model.delay,
                           ajournal.start, StatisticJournalFloat)
            if 'wind_speed' in vary:
                loader.add(T.WIND_SPEED, Units.MS, Summaries.AVG, ajournal, model.wind_speed,
                           ajournal.start, StatisticJournalFloat)
            if 'wind_heading' in vary:
                loader.add(T.WIND_HEADING, Units.DEG, Summaries.AVG, ajournal, model.wind_heading,
                           ajournal.start, StatisticJournalFloat)
            fields = fields + ((T.PREDICTED_HEART_RATE, Units.BPM, None,
                                'The inferred heart rate from the model.'),
                               (T.DETRENDED_HEART_RATE, Units.BPM, None,
                                'The heart rate with drift removed.'))
        # has to come after the above to get times in order
        super()._copy_results(s, ajournal, loader, df, fields=fields)

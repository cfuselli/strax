"""Utilities to help write strax tests.

Not needed during strax operation, so this file is not imported in __init__.py
"""

from itertools import accumulate
from functools import partial

import numpy as np
from boltons import iterutils
from hypothesis import strategies

from immutabledict import immutabledict
import strax


# Since we use np.cumsum to get disjoint intervals, we don't want stuff
# wrapping around to the integer boundary. Hence max_value is limited.
def sorted_bounds(disjoint=False,
                  max_value=50,
                  max_len=10,
                  remove_duplicates=False):
    if disjoint:
        # Since we accumulate later:
        max_value /= max_len

    s = strategies.lists(strategies.integers(min_value=0,
                                             max_value=max_value),
                         min_size=0, max_size=20)
    if disjoint:
        s = s.map(accumulate).map(list)

    # Select only cases with even-length lists
    s = s.filter(lambda x: len(x) % 2 == 0)

    # Convert to list of 2-tuples
    s = s.map(lambda x: [tuple(q)
                         for q in iterutils.chunked(sorted(x), size=2)])

    # Remove cases with zero-length intervals
    s = s.filter(lambda x: all([a[0] != a[1] for a in x]))

    if remove_duplicates:
        # (this will always succeed if disjoint=True)
        s = s.filter(lambda x: x == list(set(x)))

    # Sort intervals and result
    return s.map(sorted)


##
# Fake intervals
##

# TODO: isn't this duplicated with bounds_to_records??

def bounds_to_intervals(bs, dtype=strax.interval_dtype):
    x = np.zeros(len(bs), dtype=dtype)
    x['time'] = [x[0] for x in bs]
    # Remember: exclusive right bound...
    x['length'] = [x[1] - x[0] for x in bs]
    x['dt'] = 1
    return x


sorted_intervals = sorted_bounds().map(bounds_to_intervals)

disjoint_sorted_intervals = sorted_bounds(disjoint=True).\
    map(bounds_to_intervals)

fake_hits = sorted_bounds().map(partial(bounds_to_intervals,
                                        dtype=strax.hit_dtype))

##
# Fake pulses with 0 or 1 as waveform (e.g. to test hitfinder)
##


def bounds_to_records(bs, single=False, single_channel=False):
    """Return strax records corresponding to a list of 2-tuples
    of boundaries.

    By default, for each boundary tuple, create a pulse whose data is 1 inside.
    The pulses are put in different channels, first in 0, second in 1, etc.

    :param single: if True, instead create a single pulse in channel 0
    whose data is 1 inside the given bounds and zero outside.
    TODO: length etc. is not properly set in the single=True mode!
    TODO: this probably needs tests itself...

    :param single_channel: if True, instead create all pulses in channel 0
    You should only feed in disjoint bounds when using this.
    """
    if not len(bs):
        n_samples = 0
    else:
        n_samples = max([a for b in bs for a in b])
        if n_samples % 2:
            # Make sure we sometimes end in zero
            # TODO: not a great way to do it, you miss other cases..
            n_samples += 1
    if not single:
        # Each bound gets its own pulse, in its own channel
        recs = np.zeros(len(bs), dtype=strax.record_dtype(n_samples))
        for i, (l, r) in enumerate(bs):
            # Add waveform roughly in the center
            length = r - l  # Exclusive right bound, no + 1
            pad = (n_samples - (r - l)) // 2
            recs[i]['time'] = l
            recs[i]['length'] = pad + length
            recs[i]['data'][pad:pad+length] = 1
            assert recs[i]['data'].sum() == length
            recs[i]['channel'] = 0 if single_channel else i
        if not single_channel:
            assert len(np.unique(recs['channel'])) == len(bs)
    else:
        # Make a single record with 1 inside the bounds, 0 outside
        recs = np.zeros(1, dtype=strax.record_dtype(n_samples))
        for l, r in bs:
            recs[0]['data'][l:r] = 1
        recs[0]['time'] = 0
        recs[0]['length'] = n_samples

    recs['dt'] = 1
    return recs


single_fake_pulse = sorted_bounds()\
    .map(partial(bounds_to_records, single=True))

several_fake_records = sorted_bounds().map(bounds_to_records)

several_fake_records_one_channel = sorted_bounds(
    disjoint=True).map(
        partial(bounds_to_records, single_channel=True))


##
# Basic test plugins
##
@strax.takes_config(
    strax.Option('crash', default=False),
    strax.Option('secret_time_offset', default=0, track=False)
)
class Records(strax.Plugin):
    provides = 'records'
    parallel = 'process'
    depends_on = tuple()
    dtype = strax.record_dtype()

    def source_finished(self):
        return True

    def is_ready(self, chunk_i):
        return chunk_i < n_chunks

    def compute(self, chunk_i):
        if self.config['crash']:
            raise SomeCrash("CRASH!!!!")
        r = np.zeros(recs_per_chunk, self.dtype)
        t0 = chunk_i + self.config['secret_time_offset']
        r['time'] = t0
        r['length'] = r['dt'] = 1
        r['channel'] = np.arange(len(r))
        return self.chunk(start=t0, end=t0 + 1, data=r)


class SomeCrash(Exception):
    pass


@strax.takes_config(
    strax.Option('base_area', default=0),
    strax.Option('give_wrong_dtype', default=False),
    strax.Option('bonus_area', default_by_run=[(0, 0), (1, 1)]))
class Peaks(strax.Plugin):
    provides = 'peaks'
    data_kind = 'peaks'
    depends_on = ('records',)
    dtype = strax.peak_dtype()
    parallel = True

    def compute(self, records):
        if self.config['give_wrong_dtype']:
            return np.zeros(5, [('a', np.int), ('b', np.float)])
        p = np.zeros(len(records), self.dtype)
        p['time'] = records['time']
        p['length'] = p['dt'] = 1
        p['area'] = self.config['base_area'] + self.config['bonus_area']
        return p


# Another peak-kind plugin, to test time_range selection
# with unaligned chunks
class PeakClassification(strax.Plugin):
    provides = 'peak_classification'
    data_kind = 'peaks'
    depends_on = ('peaks',)
    dtype = (
        [('type', np.int8, 'Classification of the peak.')]
        + strax.time_fields)
    rechunk_on_save = True

    def compute(self, peaks):
        return dict(type=np.zeros(len(peaks)),
                    time=peaks['time'],
                    endtime=strax.endtime(peaks))


recs_per_chunk = 10
n_chunks = 10
run_id = '0'


##
# Some test plugins to check
# inheritance of "child"-plugins.
##

# Parent:
@strax.takes_config(
    strax.Option('by_child_overwrite_option', default=2,
                 help="Option we will overwrite in our child plugin"),
    strax.Option('parent_unique_option', type=int, default=10,
                 help='Option which is not touched by the child and '
                      'therefore the same for parent and child'),
    strax.Option('context_option', type=int,
                 help='Tracked context option e.g. n_pmts_tpc.'),
    strax.Option('more_special_context_option', track=False, type=immutabledict,
                 help="Special context option which is not tacked e.g. channel_map"))
class ParentPlugin(strax.Plugin):
    provides = 'peaks_parent'
    depends_on = 'peaks'
    parallel = True
    __version__ = '0.0.5'

    def infer_dtype(self):
        self.dtype = strax.peak_dtype(n_channels=self.config['context_option'])
        return self.dtype

    def compute(self, peaks):
        res = np.zeros(len(peaks), self.dtype)

        # Some properties we wont touch in the child:
        res['time'] = peaks['time']
        res['dt'] = peaks['dt']
        res['length'] = peaks['length']
        res['max_gap'] = self.config['parent_unique_option']

        # Properties we will modify via changed options:
        res['channel'] = peaks['channel'] + self.config['more_special_context_option']['tpc'][1]
        res['area'] = self.config['by_child_overwrite_option']

        # Shape which we will change for child:
        start, end = self.config['more_special_context_option']['tpc']
        res['area_per_channel'][:, start:end] = 1

        return res


# Child:
@strax.takes_config(
    strax.Option('by_child_overwrite_option_child', default=4, child_option=True,
                 help="Option we will overwrite in our child plugin"),
    strax.Option('context_option_child', type=int, default=10, child_option=True,
                 help='Tracked context option e.g. n_pmts_tpc.'),
    strax.Option('child_exclusive_option', type=int, default=6,
                 help='Option which is exclusive for the child.'),
    strax.Option('2nd_child_exclusive_option_child', default=2,
                 help='Same as before but end with _child'),
    strax.Option('more_special_context_option_child', child_option=True,
                 track=False,
                 default=immutabledict(tpc=(4, 10)), type=immutabledict,
                 help="iSpecial context option which is not tacked e.g. channel_map")
)
class ChildPlugin(ParentPlugin):
    provides = 'peaks_child'
    depends_on = 'peaks'
    parallel = True
    __version__ = '0.0.1'
    child_ends_with = '_child'

    def infer_dtype(self):
        # Loading here another config which will be different for child:
        self.dtype = strax.peak_dtype(n_channels=self.config['context_option_child'])
        return self.dtype

    def compute(self, peaks):
        res = super().compute(peaks)

        # Checking if an array shape can be different for parent and child:
        peaks_child = np.zeros(len(peaks), dtype=self.dtype)

        # Things which should stay the same:
        peaks_child['time'] = res['time']
        peaks_child['dt'] = res['dt']
        peaks_child['length'] = res['length']
        peaks_child['max_gap'] = res['max_gap']

        # Things which should be different:
        peaks_child['area'] = res['area']
        peaks_child['channel'] = res['channel']
        peaks_child['n_hits'] = self.config['2nd_child_exclusive_option_child']

        # Test if we can change shape of child:
        start, end = self.config['more_special_context_option_child']['tpc']
        peaks_child['area_per_channel'][:, :] = res['area_per_channel'][:, :]

        return peaks_child
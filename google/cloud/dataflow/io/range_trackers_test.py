# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the range_trackers module."""

import array
import copy
import logging
import unittest


from google.cloud.dataflow.io import range_trackers


class OffsetRangeTrackerTest(unittest.TestCase):

  def test_try_return_record_simple_sparse(self):
    tracker = range_trackers.OffsetRangeTracker(100, 200)
    self.assertTrue(tracker.try_return_record_at(True, 110))
    self.assertTrue(tracker.try_return_record_at(True, 140))
    self.assertTrue(tracker.try_return_record_at(True, 183))
    self.assertFalse(tracker.try_return_record_at(True, 210))

  def test_try_return_record_simple_dense(self):
    tracker = range_trackers.OffsetRangeTracker(3, 6)
    self.assertTrue(tracker.try_return_record_at(True, 3))
    self.assertTrue(tracker.try_return_record_at(True, 4))
    self.assertTrue(tracker.try_return_record_at(True, 5))
    self.assertFalse(tracker.try_return_record_at(True, 6))

  def test_try_return_record_continuous_until_split_point(self):
    tracker = range_trackers.OffsetRangeTracker(9, 18)
    # Return records with gaps of 2; every 3rd record is a split point.
    self.assertTrue(tracker.try_return_record_at(True, 10))
    self.assertTrue(tracker.try_return_record_at(False, 12))
    self.assertTrue(tracker.try_return_record_at(False, 14))
    self.assertTrue(tracker.try_return_record_at(True, 16))
    # Out of range, but not a split point...
    self.assertTrue(tracker.try_return_record_at(False, 18))
    self.assertTrue(tracker.try_return_record_at(False, 20))
    # Out of range AND a split point.
    self.assertFalse(tracker.try_return_record_at(True, 22))

  def test_split_at_offset_fails_if_unstarted(self):
    tracker = range_trackers.OffsetRangeTracker(100, 200)
    self.assertFalse(tracker.try_split_at_position(150))

  def test_split_at_offset(self):
    tracker = range_trackers.OffsetRangeTracker(100, 200)
    self.assertTrue(tracker.try_return_record_at(True, 110))
    # Example positions we shouldn't split at, when last record starts at 110:
    self.assertFalse(tracker.try_split_at_position(109))
    self.assertFalse(tracker.try_split_at_position(110))
    self.assertFalse(tracker.try_split_at_position(200))
    self.assertFalse(tracker.try_split_at_position(210))
    # Example positions we *should* split at:
    self.assertTrue(copy.copy(tracker).try_split_at_position(111))
    self.assertTrue(copy.copy(tracker).try_split_at_position(129))
    self.assertTrue(copy.copy(tracker).try_split_at_position(130))
    self.assertTrue(copy.copy(tracker).try_split_at_position(131))
    self.assertTrue(copy.copy(tracker).try_split_at_position(150))
    self.assertTrue(copy.copy(tracker).try_split_at_position(199))

    # If we split at 170 and then at 150:
    self.assertTrue(tracker.try_split_at_position(170))
    self.assertTrue(tracker.try_split_at_position(150))
    # Should be able  to return a record starting before the new stop offset.
    # Returning records starting at the same offset is ok.
    self.assertTrue(copy.copy(tracker).try_return_record_at(True, 135))
    self.assertTrue(copy.copy(tracker).try_return_record_at(True, 135))
    # Should be able to return a record starting right before the new stop
    # offset.
    self.assertTrue(copy.copy(tracker).try_return_record_at(True, 149))
    # Should not be able to return a record starting at or after the new stop
    # offset.
    self.assertFalse(tracker.try_return_record_at(True, 150))
    self.assertFalse(tracker.try_return_record_at(True, 151))
    # Should accept non-splitpoint records starting after stop offset.
    self.assertTrue(tracker.try_return_record_at(False, 135))
    self.assertTrue(tracker.try_return_record_at(False, 152))
    self.assertTrue(tracker.try_return_record_at(False, 160))
    self.assertTrue(tracker.try_return_record_at(False, 171))

  def test_get_position_for_fraction_dense(self):
    # Represents positions 3, 4, 5.
    tracker = range_trackers.OffsetRangeTracker(3, 6)
    # [3, 3) represents 0.0 of [3, 6)
    self.assertEqual(3, tracker.get_position_for_fraction_consumed(0.0))
    # [3, 4) represents up to 1/3 of [3, 6)
    self.assertEqual(4, tracker.get_position_for_fraction_consumed(1.0 / 6))
    self.assertEqual(4, tracker.get_position_for_fraction_consumed(0.333))
    # [3, 5) represents up to 2/3 of [3, 6)
    self.assertEqual(5, tracker.get_position_for_fraction_consumed(0.334))
    self.assertEqual(5, tracker.get_position_for_fraction_consumed(0.666))
    # Any fraction consumed over 2/3 means the whole [3, 6) has been consumed.
    self.assertEqual(6, tracker.get_position_for_fraction_consumed(0.667))

  def test_get_fraction_consumed_dense(self):
    tracker = range_trackers.OffsetRangeTracker(3, 6)
    self.assertEqual(0, tracker.fraction_consumed)
    self.assertTrue(tracker.try_return_record_at(True, 3))
    self.assertEqual(0.0, tracker.fraction_consumed)
    self.assertTrue(tracker.try_return_record_at(True, 4))
    self.assertEqual(1.0 / 3, tracker.fraction_consumed)
    self.assertTrue(tracker.try_return_record_at(True, 5))
    self.assertEqual(2.0 / 3, tracker.fraction_consumed)
    self.assertTrue(tracker.try_return_record_at(False, 6))  # non-split-point
    self.assertEqual(1.0, tracker.fraction_consumed)
    self.assertTrue(tracker.try_return_record_at(False, 7))  # non-split-point
    self.assertFalse(tracker.try_return_record_at(True, 7))

  def test_get_fraction_consumed_sparse(self):
    tracker = range_trackers.OffsetRangeTracker(100, 200)
    self.assertEqual(0, tracker.fraction_consumed)
    self.assertTrue(tracker.try_return_record_at(True, 110))
    # Consumed positions through 110 = total 10 positions of 100 done.
    self.assertEqual(0.10, tracker.fraction_consumed)
    self.assertTrue(tracker.try_return_record_at(True, 150))
    self.assertEqual(0.50, tracker.fraction_consumed)
    self.assertTrue(tracker.try_return_record_at(True, 195))
    self.assertEqual(0.95, tracker.fraction_consumed)

  def test_everything_with_unbounded_range(self):
    tracker = range_trackers.OffsetRangeTracker(
        100,
        range_trackers.OffsetRangeTracker.OFFSET_INFINITY)
    self.assertTrue(tracker.try_return_record_at(True, 150))
    self.assertTrue(tracker.try_return_record_at(True, 250))
    # get_position_for_fraction_consumed should fail for an unbounded range
    with self.assertRaises(Exception):
      tracker.get_position_for_fraction_consumed(0.5)

  def test_try_return_first_record_not_split_point(self):
    with self.assertRaises(Exception):
      range_trackers.OffsetRangeTracker(100, 200).try_return_record_at(
          False, 120)

  def test_try_return_record_non_monotonic(self):
    tracker = range_trackers.OffsetRangeTracker(100, 200)
    tracker.try_return_record_at(True, 120)
    with self.assertRaises(Exception):
      tracker.try_return_record_at(True, 110)


class GroupedShuffleRangeTrackerTest(unittest.TestCase):

  def bytes_to_position(self, bytes_array):
    return array.array('B', bytes_array).tostring()

  def test_try_return_record_in_infinite_range(self):
    tracker = range_trackers.GroupedShuffleRangeTracker('', '')
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([1, 2, 3])))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([1, 2, 5])))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([3, 6, 8, 10])))

  def test_try_return_record_finite_range(self):
    tracker = range_trackers.GroupedShuffleRangeTracker(
        self.bytes_to_position([1, 0, 0]), self.bytes_to_position([5, 0, 0]))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([1, 2, 3])))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([1, 2, 5])))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([3, 6, 8, 10])))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([4, 255, 255, 255])))
    # Should fail for positions that are lexicographically equal to or larger
    # than the defined stop position.
    self.assertFalse(copy.copy(tracker).try_return_record_at(
        True, self.bytes_to_position([5, 0, 0])))
    self.assertFalse(copy.copy(tracker).try_return_record_at(
        True, self.bytes_to_position([5, 0, 1])))
    self.assertFalse(copy.copy(tracker).try_return_record_at(
        True, self.bytes_to_position([6, 0, 0])))

  def test_try_return_record_with_non_split_point(self):
    tracker = range_trackers.GroupedShuffleRangeTracker(
        self.bytes_to_position([1, 0, 0]), self.bytes_to_position([5, 0, 0]))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([1, 2, 3])))
    self.assertTrue(tracker.try_return_record_at(
        False, self.bytes_to_position([1, 2, 3])))
    self.assertTrue(tracker.try_return_record_at(
        False, self.bytes_to_position([1, 2, 3])))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([1, 2, 5])))
    self.assertTrue(tracker.try_return_record_at(
        False, self.bytes_to_position([1, 2, 5])))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([3, 6, 8, 10])))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([4, 255, 255, 255])))

  def test_first_record_non_split_point(self):
    tracker = range_trackers.GroupedShuffleRangeTracker(
        self.bytes_to_position([3, 0, 0]), self.bytes_to_position([5, 0, 0]))
    with self.assertRaises(ValueError):
      tracker.try_return_record_at(False, self.bytes_to_position([3, 4, 5]))

  def test_non_split_point_record_with_different_position(self):
    tracker = range_trackers.GroupedShuffleRangeTracker(
        self.bytes_to_position([3, 0, 0]), self.bytes_to_position([5, 0, 0]))
    tracker.try_return_record_at(True, self.bytes_to_position([3, 4, 5]))
    with self.assertRaises(ValueError):
      tracker.try_return_record_at(False, self.bytes_to_position([3, 4, 6]))

  def test_try_return_record_before_start(self):
    tracker = range_trackers.GroupedShuffleRangeTracker(
        self.bytes_to_position([3, 0, 0]), self.bytes_to_position([5, 0, 0]))
    with self.assertRaises(ValueError):
      tracker.try_return_record_at(True, self.bytes_to_position([1, 2, 3]))

  def test_try_return_non_monotonic(self):
    tracker = range_trackers.GroupedShuffleRangeTracker(
        self.bytes_to_position([3, 0, 0]), self.bytes_to_position([5, 0, 0]))
    tracker.try_return_record_at(True, self.bytes_to_position([3, 4, 5]))
    tracker.try_return_record_at(True, self.bytes_to_position([3, 4, 6]))
    with self.assertRaises(ValueError):
      tracker.try_return_record_at(True, self.bytes_to_position([3, 2, 1]))

  def test_try_return_identical_positions(self):
    tracker = range_trackers.GroupedShuffleRangeTracker(
        self.bytes_to_position([3, 0, 0]), self.bytes_to_position([5, 0, 0]))
    tracker.try_return_record_at(True, self.bytes_to_position([3, 4, 5]))
    with self.assertRaises(ValueError):
      tracker.try_return_record_at(True, self.bytes_to_position([3, 4, 5]))

  def test_try_split_at_position_infinite_range(self):
    tracker = range_trackers.GroupedShuffleRangeTracker('', '')
    # Should fail before first record is returned.
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([3, 4, 5, 6])))

    tracker.try_return_record_at(True, self.bytes_to_position([1, 2, 3]))

    # Should now succeed.
    self.assertTrue(tracker.try_split_at_position(
        self.bytes_to_position([3, 4, 5, 6])))
    # Should not split at same or larger position.
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([3, 4, 5, 6])))
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([3, 4, 5, 6, 7])))
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([4, 5, 6, 7])))

    # Should split at smaller position.
    self.assertTrue(tracker.try_split_at_position(
        self.bytes_to_position([3, 2, 1])))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([2, 3, 4])))

    # Should not split at a position we're already past.
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([2, 3, 4])))
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([2, 3, 3])))

    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([3, 2, 0])))
    self.assertFalse(tracker.try_return_record_at(
        True, self.bytes_to_position([3, 2, 1])))

  def test_try_test_split_at_position_finite_range(self):
    tracker = range_trackers.GroupedShuffleRangeTracker(
        self.bytes_to_position([0, 0, 0]),
        self.bytes_to_position([10, 20, 30]))
    # Should fail before first record is returned.
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([0, 0, 0])))
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([3, 4, 5, 6])))

    tracker.try_return_record_at(True, self.bytes_to_position([1, 2, 3]))

    # Should now succeed.
    self.assertTrue(tracker.try_split_at_position(
        self.bytes_to_position([3, 4, 5, 6])))
    # Should not split at same or larger position.
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([3, 4, 5, 6])))
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([3, 4, 5, 6, 7])))
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([4, 5, 6, 7])))

    # Should split at smaller position.
    self.assertTrue(tracker.try_split_at_position(
        self.bytes_to_position([3, 2, 1])))
    # But not at a position at or before last returned record.
    self.assertFalse(tracker.try_split_at_position(
        self.bytes_to_position([1, 2, 3])))

    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([2, 3, 4])))
    self.assertTrue(tracker.try_return_record_at(
        True, self.bytes_to_position([3, 2, 0])))
    self.assertFalse(tracker.try_return_record_at(
        True, self.bytes_to_position([3, 2, 1])))


if __name__ == '__main__':
  logging.getLogger().setLevel(logging.INFO)
  unittest.main()

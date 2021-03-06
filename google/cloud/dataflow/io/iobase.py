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

"""Sources and sinks.

A Source manages record-oriented data input from a particular kind of source
(e.g. a set of files, a database table, etc.). The reader() method of a source
returns a reader object supporting the iterator protocol; iteration yields
raw records of unprocessed, serialized data.


A Sink manages record-oriented data output to a particular kind of sink
(e.g. a set of files, a database table, etc.). The writer() method of a sink
returns a writer object supporting writing records of serialized data to
the sink.
"""

import logging
import uuid

from google.cloud.dataflow import pvalue
from google.cloud.dataflow.pvalue import AsIter
from google.cloud.dataflow.pvalue import AsSingleton
from google.cloud.dataflow.transforms import core
from google.cloud.dataflow.transforms import ptransform
from google.cloud.dataflow.transforms import window


def _dict_printable_fields(dict_object, skip_fields):
  """Returns a list of strings for the interesting fields of a dict."""
  return ['%s=%r' % (name, value)
          for name, value in dict_object.iteritems()
          # want to output value 0 but not None nor []
          if (value or value == 0)
          and name not in skip_fields]

_minor_fields = ['coder', 'key_coder', 'value_coder',
                 'config_bytes', 'elements',
                 'append_trailing_newlines', 'strip_trailing_newlines',
                 'compression_type']


class NativeSource(object):
  """A source implemented by Dataflow service.

  This class is to be only inherited by sources natively implemented by Cloud
  Dataflow service, hence should not be sub-classed by users.

  This class is deprecated and should not be used to define new sources.
  """

  def reader(self):
    """Returns a NativeSourceReader instance associated with this source."""
    raise NotImplementedError

  def __repr__(self):
    return '<{name} {vals}>'.format(
        name=self.__class__.__name__,
        vals=', '.join(_dict_printable_fields(self.__dict__,
                                              _minor_fields)))


class NativeSourceReader(object):
  """A reader for a source implemented by Dataflow service."""

  def __enter__(self):
    """Opens everything necessary for a reader to function properly."""
    raise NotImplementedError

  def __exit__(self, exception_type, exception_value, traceback):
    """Cleans up after a reader executed."""
    raise NotImplementedError

  def __iter__(self):
    """Returns an iterator over all the records of the source."""
    raise NotImplementedError

  @property
  def returns_windowed_values(self):
    """Returns whether this reader returns windowed values."""
    return False

  def get_progress(self):
    """Returns a representation of how far the reader has read.

    Returns:
      A SourceReaderProgress object that gives the current progress of the
      reader.
    """
    return

  def request_dynamic_split(self, dynamic_split_request):
    """Attempts to split the input in two parts.

    The two parts are named the "primary" part and the "residual" part. The
    current 'NativeSourceReader' keeps processing the primary part, while the
    residual part will be processed elsewhere (e.g. perhaps on a different
    worker).

    The primary and residual parts, if concatenated, must represent the
    same input as the current input of this 'NativeSourceReader' before this
    call.

    The boundary between the primary part and the residual part is
    specified in a framework-specific way using 'DynamicSplitRequest' e.g.,
    if the framework supports the notion of positions, it might be a
    position at which the input is asked to split itself (which is not
    necessarily the same position at which it *will* split itself); it
    might be an approximate fraction of input, or something else.

    This function returns a 'DynamicSplitResult', which encodes, in a
    framework-specific way, the information sufficient to construct a
    description of the resulting primary and residual inputs. For example, it
    might, again, be a position demarcating these parts, or it might be a pair
    of fully-specified input descriptions, or something else.

    After a successful call to 'request_dynamic_split()', subsequent calls
    should be interpreted relative to the new primary.

    Args:
      dynamic_split_request: A 'DynamicSplitRequest' describing the split
        request.

    Returns:
      'None' if the 'DynamicSplitRequest' cannot be honored (in that
      case the input represented by this 'NativeSourceReader' stays the same),
      or a 'DynamicSplitResult' describing how the input was split into a
      primary and residual part.
    """
    logging.debug(
        'SourceReader %r does not support dynamic splitting. Ignoring dynamic '
        'split request: %r',
        self, dynamic_split_request)
    return


class ReaderProgress(object):
  """A representation of how far a NativeSourceReader has read."""

  def __init__(self, position=None, percent_complete=None, remaining_time=None):

    self._position = position

    if percent_complete is not None:
      percent_complete = float(percent_complete)
      if percent_complete < 0 or percent_complete > 1:
        raise ValueError(
            'The percent_complete argument was %f. Must be in range [0, 1].'
            % percent_complete)
    self._percent_complete = percent_complete

    self._remaining_time = remaining_time

  @property
  def position(self):
    """Returns progress, represented as a ReaderPosition object."""
    return self._position

  @property
  def percent_complete(self):
    """Returns progress, represented as a percentage of total work.

    Progress range from 0.0 (beginning, nothing complete) to 1.0 (end of the
    work range, entire WorkItem complete).

    Returns:
      Progress represented as a percentage of total work.
    """
    return self._percent_complete

  @property
  def remaining_time(self):
    """Returns progress, represented as an estimated time remaining."""
    return self._remaining_time


class ReaderPosition(object):
  """A representation of position in an iteration of a 'NativeSourceReader'."""

  def __init__(self, end=None, key=None, byte_offset=None, record_index=None,
               shuffle_position=None, concat_position=None):
    """Initializes ReaderPosition.

    A ReaderPosition may get instantiated for one of these position types. Only
    one of these should be specified.

    Args:
      end: position is past all other positions. For example, this may be used
        to represent the end position of an unbounded range.
      key: position is a string key.
      byte_offset: position is a byte offset.
      record_index: position is a record index
      shuffle_position: position is a base64 encoded shuffle position.
      concat_position: position is a 'ConcatPosition'.
    """

    self.end = end
    self.key = key
    self.byte_offset = byte_offset
    self.record_index = record_index
    self.shuffle_position = shuffle_position

    if concat_position is not None:
      assert isinstance(concat_position, ConcatPosition)
    self.concat_position = concat_position


class ConcatPosition(object):
  """A position that encapsulate an inner position and an index.

  This is used to represent the position of a source that encapsulate several
  other sources.
  """

  def __init__(self, index, position):
    """Initializes ConcatPosition.

    Args:
      index: index of the source currently being read.
      position: inner position within the source currently being read.
    """

    if position is not None:
      assert isinstance(position, ReaderPosition)
    self.index = index
    self.position = position


class DynamicSplitRequest(object):
  """Specifies how 'NativeSourceReader.request_dynamic_split' should split.
  """

  def __init__(self, progress):
    assert isinstance(progress, ReaderProgress)
    self.progress = progress


class DynamicSplitResult(object):
  pass


class DynamicSplitResultWithPosition(DynamicSplitResult):

  def __init__(self, stop_position):
    assert isinstance(stop_position, ReaderPosition)
    self.stop_position = stop_position


class NativeSink(object):
  """A sink implemented by Dataflow service.

  This class is to be only inherited by sinks natively implemented by Cloud
  Dataflow service, hence should not be sub-classed by users.
  """

  def writer(self):
    """Returns a SinkWriter for this source."""
    raise NotImplementedError

  def __repr__(self):
    return '<{name} {vals}>'.format(
        name=self.__class__.__name__,
        vals=_dict_printable_fields(self.__dict__, _minor_fields))


class NativeSinkWriter(object):
  """A writer for a sink implemented by Dataflow service."""

  def __enter__(self):
    """Opens everything necessary for a writer to function properly."""
    raise NotImplementedError

  def __exit__(self, exception_type, exception_value, traceback):
    """Cleans up after a writer executed."""
    raise NotImplementedError

  @property
  def takes_windowed_values(self):
    """Returns whether this writer takes windowed values."""
    return False

  def Write(self, o):  # pylint: disable=invalid-name
    """Writes a record to the sink associated with this writer."""
    raise NotImplementedError


class RangeTracker(object):
  """A thread-safe helper object for implementing dynamic work rebalancing.

  **Usage of the RangeTracker class hierarchy**

  The ``RangeTracker`` class should not be used per se---all users should use
  its subclasses directly. We declare it here because all subclasses have
  roughly the same interface and the same properties, to centralize the
  documentation.

  Currently we provide one implementation: ``iobase.OffsetRangeTracker``.

  **Position-based sources**

  A position-based source is one where the source can be described by a range
  of positions of an ordered type and the records returned by the reader can be
  described by positions of the same type.

  In case a record occupies a range of positions in the source, the most
  important thing about the record is the position where it starts.

  Defining the semantics of positions for a source is entirely up to the source
  class, however the chosen definitions have to obey certain properties in order
  to make it possible to correctly split the source into parts, including
  dynamic splitting. Two main aspects need to be defined:

  1. How to assign starting positions to records.
  2. Which records should be read by a source with a range '[A, B)'.

  Moreover, reading a range must be *efficient*, i.e., the performance of
  reading a range should not significantly depend on the location of the range.
  For example, reading the range [A, B) should not require reading all data
  before 'A'.

  The sections below explain exactly what properties these definitions must
  satisfy, and how to use a ``RangeTracker`` with a properly defined source.

  **Properties of position-based sources**

  The main requirement for position-based sources is *associativity*: reading
  records from '[A, B)' and records from '[B, C)' should give the same
  records as reading from '[A, C)', where 'A <= B <= C'. This property
  ensures that no matter how a range of positions is split into arbitrarily many
  sub-ranges, the total set of records described by them stays the same.

  The other important property is how the source's range relates to positions of
  records in the source. In many sources each record can be identified by a
  unique starting position. In this case:

  * All records returned by a source '[A, B)' must have starting positions in
    this range.
  * All but the last record should end within this range. The last record may or
    may not extend past the end of the range.
  * Records should not overlap.

  Such sources should define "read '[A, B)'" as "read from the first record
  starting at or after 'A', up to but not including the first record starting
  at or after 'B'".

  Some examples of such sources include reading lines or CSV from a text file,
  reading keys and values from a BigTable, etc.

  The concept of *split points* allows to extend the definitions for dealing
  with sources where some records cannot be identified by a unique starting
  position.

  In all cases, all records returned by a source '[A, B)' must *start* at or
  after 'A'.

  **Split points**

  Some sources may have records that are not directly addressable. For example,
  imagine a file format consisting of a sequence of compressed blocks. Each
  block can be assigned an offset, but records within the block cannot be
  directly addressed without decompressing the block. Let us refer to this
  hypothetical format as <i>CBF (Compressed Blocks Format)</i>.

  Many such formats can still satisfy the associativity property. For example,
  in CBF, reading '[A, B)' can mean "read all the records in all blocks whose
  starting offset is in '[A, B)'".

  To support such complex formats, we introduce the notion of *split points*. We
  say that a record is a split point if there exists a position 'A' such that
  the record is the first one to be returned when reading the range
  '[A, infinity)'. In CBF, the only split points would be the first records
  in each block.

  Split points allow us to define the meaning of a record's position and a
  source's range in all cases:

  * For a record that is at a split point, its position is defined to be the
    largest 'A' such that reading a source with the range '[A, infinity)'
    returns this record.
  * Positions of other records are only required to be non-decreasing.
  * Reading the source '[A, B)' must return records starting from the first
    split point at or after 'A', up to but not including the first split point
    at or after 'B'. In particular, this means that the first record returned
    by a source MUST always be a split point.
  * Positions of split points must be unique.

  As a result, for any decomposition of the full range of the source into
  position ranges, the total set of records will be the full set of records in
  the source, and each record will be read exactly once.

  **Consumed positions**

  As the source is being read, and records read from it are being passed to the
  downstream transforms in the pipeline, we say that positions in the source are
  being *consumed*. When a reader has read a record (or promised to a caller
  that a record will be returned), positions up to and including the record's
  start position are considered *consumed*.

  Dynamic splitting can happen only at *unconsumed* positions. If the reader
  just returned a record at offset 42 in a file, dynamic splitting can happen
  only at offset 43 or beyond, as otherwise that record could be read twice (by
  the current reader and by a reader of the task starting at 43).
  """

  def start_position(self):
    """Returns the starting position of the current range, inclusive."""
    raise NotImplementedError

  def stop_position(self):
    """Returns the ending position of the current range, exclusive."""
    raise NotImplementedError

  def try_return_record_at(self, is_at_split_point, record_start):
    """Atomically determines if a record at the given position can be returned.

    Additionally, Updates the internal state of the ``RangeTracker``.

    In particular:

    * If ``is_at_split_point`` is ``True``, and ``record_start`` is outside the
      current range, returns ``False``;
    * Otherwise, updates the last-consumed position to ``record_start`` and
      returns ``True``.

    This method MUST be called on all split point records. It may be called on
    every record.

    Method ``try_return_record_at`` and method ``try_split_at_position`` will be
    accessed by different threads and implementor must ensure that only one of
    these methods is executed at a given time.

    Args:
      is_at_split_point: ``True`` if record is at a split point, ``False``
      otherwise.

      record_start: starting position of the record.
    """
    raise NotImplementedError

  def try_split_at_position(self, split_position):
    """Atomically splits the current range.

    Splits the current range '[get_start_position(), get_stop_position())'
    into a "primary" part '[get_start_position(), split_position())' and a
    "residual" part '[split_position(), get_stop_position())', assuming the
    current last-consumed position is within
    '[get_start_position(), split_position())' (i.e., 'split_position()'
    has not been consumed yet).

    Updates the current range to be the primary and returns ``True``. This
    means that all further calls on the current object will interpret their
    arguments relative to the primary range.

    If the split position has already been consumed, or if no
    ``try_return_record_at`` call was made yet, returns ``False``. The
    second condition is to prevent dynamic splitting during reader start-up.

    Method ``try_return_record_at`` and method ``try_split_at_position`` will be
    accessed by different threads and implementor must ensure that only one of
    these methods is executed at a given time.

    Args:
      split_position: an instance of ReaderPosition that gives the position
      where the current range should be split at.
    """
    raise NotImplementedError

  def fraction_consumed(self):
    """Returns the approximate fraction of consumed positions in the source.

    Returns the approximate fraction of positions that have been consumed by
    successful 'try_return_record_at()' calls, or 0.0 if no such calls have
    happened.
    """
    raise NotImplementedError


class Sink(object):
  """A resource that can be written to using the ``df.io.Write`` transform.

  Here ``df`` stands for Dataflow Python code imported in following manner.
  ``import google.cloud.dataflow as df``.

  A parallel write to an ``iobase.Sink`` consists of three phases:

  1. A sequential *initialization* phase (e.g., creating a temporary output
     directory, etc.)
  2. A parallel write phase where workers write *bundles* of records
  3. A sequential *finalization* phase (e.g., committing the writes, merging
     output files, etc.)

  For exact definition of a Dataflow bundle please see
  https://cloud.google.com/dataflow/faq.

  Implementing a new sink requires extending two classes.

  1. iobase.Sink

  ``iobase.Sink`` is an immutable logical description of the location/resource
  to write to. Depending on the type of sink, it may contain fields such as the
  path to an output directory on a filesystem, a database table name,
  etc. ``iobase.Sink`` provides methods for performing a write operation to the
  sink described by it. To this end, implementors of an extension of
  ``iobase.Sink`` must implement three methods:
  ``initialize_write()``, ``open_writer()``, and ``finalize_write()``.

  2. iobase.Writer

  ``iobase.Writer`` is used to write a single bundle of records. An
  ``iobase.Writer`` defines two methods: ``write()`` which writes a
  single record from the bundle and ``close()`` which is called once
  at the end of writing a bundle.

  See also ``df.io.fileio.FileSink`` which provides a simpler API for writing
  sinks that produce files.

  **Execution of the Write transform**

  ``initialize_write()`` and ``finalize_write()`` are conceptually called once:
  at the beginning and end of a ``Write`` transform. However, implementors must
  ensure that these methods are *idempotent*, as they may be called multiple
  times on different machines in the case of failure/retry or for redundancy.

  ``initialize_write()`` should perform any initialization that needs to be done
  prior to writing to the sink. ``initialize_write()`` may return a result
  (let's call this ``init_result``) that contains any parameters it wants to
  pass on to its writers about the sink. For example, a sink that writes to a
  file system may return an ``init_result`` that contains a dynamically
  generated unique directory to which data should be written.

  To perform writing of a bundle of elements, Dataflow execution engine will
  create an ``iobase.Writer`` using the implementation of
  ``iobase.Sink.open_writer()``. When invoking ``open_writer()`` execution
  engine will provide the ``init_result`` returned by ``initialize_write()``
  invocation as well as a *bundle id* (let's call this ``bundle_id``) that is
  unique for each invocation of ``open_writer()``.

  Execution engine will then invoke ``iobase.Writer.write()`` implementation for
  each element that has to be written. Once all elements of a bundle are
  written, execution engine will invoke ``iobase.Writer.close()`` implementation
  which should return a result (let's call this ``write_result``) that contains
  information that encodes the result of the write and, in most cases, some
  encoding of the unique bundle id. For example, if each bundle is written to a
  unique temporary file, ``close()`` method may return an object that contains
  the temporary file name. After writing of all bundles is complete, execution
  engine will invoke ``finalize_write()`` implementation. As parameters to this
  invocation execution engine will provide ``init_result`` as well as an
  iterable of ``write_result``.

  The execution of a write transform can be illustrated using following pseudo
  code (assume that the outer for loop happens in parallel across many
  machines)::

    init_result = sink.initialize_write()
    write_results = []
    for bundle in partition(pcoll):
      writer = sink.open_writer(init_result, generate_bundle_id())
      for elem in bundle:
        writer.write(elem)
      write_results.append(writer.close())
    sink.finalize_write(init_result, write_results)


  **init_result**

  Methods of 'iobase.Sink' should agree on the 'init_result' type that will be
  returned when initializing the sink. This type can be a client-defined object
  or an existing type. The returned type must be picklable using Dataflow coder
  ``coders.PickleCoder``. Returning an init_result is optional.

  **bundle_id**

  In order to ensure fault-tolerance, a bundle may be executed multiple times
  (e.g., in the event of failure/retry or for redundancy). However, exactly one
  of these executions will have its result passed to the
  ``iobase.Sink.finalize_write()`` method. Each call to
  ``iobase.Sink.open_writer()`` is passed a unique bundle id when it is called
  by the ``WriteImpl`` transform, so even redundant or retried bundles will have
  a unique way of identifying their output.

  The bundle id should be used to guarantee that a bundle's output is unique.
  This uniqueness guarantee is important; if a bundle is to be output to a file,
  for example, the name of the file must be unique to avoid conflicts with other
  writers. The bundle id should be encoded in the writer result returned by the
  writer and subsequently used by the ``finalize_write()`` method to identify
  the results of successful writes.

  For example, consider the scenario where a Writer writes files containing
  serialized records and the ``finalize_write()`` is to merge or rename these
  output files. In this case, a writer may use its unique id to name its output
  file (to avoid conflicts) and return the name of the file it wrote as its
  writer result. The ``finalize_write()`` will then receive an ``Iterable`` of
  output file names that it can then merge or rename using some bundle naming
  scheme.

  **write_result**

  ``iobase.Writer.close()`` and ``finalize_write()`` implementations must agree
  on type of the ``write_result`` object returned when invoking
  ``iobase.Writer.close()``. This type can be a client-defined object or
  an existing type. The returned type must be picklable using Dataflow coder
  ``coders.PickleCoder``. Returning a ``write_result`` when
  ``iobase.Writer.close()`` is invoked is optional but if unique
  ``write_result`` objects are not returned, sink should, guarantee idempotency
  when same bundle is written multiple times due to failure/retry or redundancy.


  **More information**

  For more information on creating new sinks please refer to the official
  documentation at
  ``https://cloud.google.com/dataflow/model/custom-io#creating-sinks``.
  """

  def initialize_write(self):
    """Initializes the sink before writing begins.

    Invoked before any data is written to the sink.


    Please see documentation in ``iobase.Sink`` for an example.

    Returns:
      An object that contains any sink specific state generated by
      initialization. This object will be passed to open_writer() and
      finalize_write() methods.
    """
    raise NotImplementedError

  def open_writer(self, init_result, uid):
    """Opens a writer for writing a bundle of elements to the sink.

    Args:
      init_result: the result of initialize_write() invocation.
      uid: a unique identifier generated by the system.
    Returns:
      an ``iobase.Writer`` that can be used to write a bundle of records to the
      current sink.
    """
    raise NotImplementedError

  def finalize_write(self, init_result, writer_results):
    """Finalizes the sink after all data is written to it.

    Given the result of initialization and an iterable of results from bundle
    writes, performs finalization after writing and closes the sink. Called
    after all bundle writes are complete.

    The bundle write results that are passed to finalize are those returned by
    bundles that completed successfully. Although bundles may have been run
    multiple times (for fault-tolerance), only one writer result will be passed
    to finalize for each bundle. An implementation of finalize should perform
    clean up of any failed and successfully retried bundles.  Note that these
    failed bundles will not have their writer result passed to finalize, so
    finalize should be capable of locating any temporary/partial output written
    by failed bundles.

    If all retries of a bundle fails, the whole pipeline will fail *without*
    finalize_write() being invoked.

    A best practice is to make finalize atomic. If this is impossible given the
    semantics of the sink, finalize should be idempotent, as it may be called
    multiple times in the case of failure/retry or for redundancy.

    Note that the iteration order of the writer results is not guaranteed to be
    consistent if finalize is called multiple times.

    Args:
      init_result: the result of ``initialize_write()`` invocation.
      writer_results: an iterable containing results of ``Writer.close()``
        invocations. This will only contain results of successful writes, and
        will only contain the result of a single successful write for a given
        bundle.
    """
    raise NotImplementedError


class Writer(object):
  """Writes a bundle of elements from a ``PCollection`` to a sink.

  A Writer  ``iobase.Writer.write()`` writes and elements to the sink while
  ``iobase.Writer.close()`` is called after all elements in the bundle have been
  written.

  See ``iobase.Sink`` for more detailed documentation about the process of
  writing to a sink.
  """

  def write(self, value):
    """Writes a value to the sink using the current writer."""
    raise NotImplementedError

  def close(self):
    """Closes the current writer.

    Please see documentation in ``iobase.Sink`` for an example.

    Returns:
      An object representing the writes that were performed by the current
      writer.
    """
    raise NotImplementedError


class _NativeWrite(ptransform.PTransform):
  """A PTransform for writing to a Dataflow native sink.

  These are sinks that are implemented natively by the Dataflow service
  and hence should not be updated by users. These sinks are processed
  using a Dataflow native write transform.

  Applying this transform results in a ``pvalue.PDone``.
  """

  def __init__(self, *args, **kwargs):
    """Initializes a Write transform.

    Args:
      *args: A tuple of position arguments.
      **kwargs: A dictionary of keyword arguments.

    The *args, **kwargs are expected to be (label, sink) or (sink).
    """
    label, sink = self.parse_label_and_arg(args, kwargs, 'sink')
    super(_NativeWrite, self).__init__(label)
    self.sink = sink

  def apply(self, pcoll):
    self._check_pcollection(pcoll)
    return pvalue.PDone(pcoll.pipeline)


class Read(ptransform.PTransform):
  """A transform that reads a PCollection."""

  def __init__(self, *args, **kwargs):
    """Initializes a Read transform.

    Args:
      *args: A tuple of position arguments.
      **kwargs: A dictionary of keyword arguments.

    The *args, **kwargs are expected to be (label, source) or (source).
    """
    label, source = self.parse_label_and_arg(args, kwargs, 'source')
    super(Read, self).__init__(label)
    self.source = source

  def apply(self, pbegin):
    assert isinstance(pbegin, pvalue.PBegin)
    self.pipeline = pbegin.pipeline
    return pvalue.PCollection(self.pipeline)

  def get_windowing(self, unused_inputs):
    return core.Windowing(window.GlobalWindows())


class Write(ptransform.PTransform):
  """A ``PTransform`` that writes to a sink.

  A sink should inherit ``iobase.Sink``. Such implementations are
  handled using a composite transform that consists of three ``ParDo``s -
  (1) a ``ParDo`` performing a global initialization (2) a ``ParDo`` performing
  a parallel write and (3) a ``ParDo`` performing a global finalization. In the
  case of an empty ``PCollection``, only the global initialization and
  finalization will be performed. Currently only batch workflows support custom
  sinks.

  Example usage::

      pcollection | df.io.Write(MySink())

  This returns a ``pvalue.PValue`` object that represents the end of the
  Pipeline.

  The sink argument may also be a full PTransform, in which case it will be
  applied directly.  This allows composite sink-like transforms (e.g. a sink
  with some pre-processing DoFns) to be used the same as all other sinks.

  This transform also supports sinks that inherit ``iobase.NativeSink``. These
  are sinks that are implemented natively by the Dataflow service and hence
  should not be updated by users. These sinks are processed using a Dataflow
  native write transform.
  """

  def __init__(self, *args, **kwargs):
    """Initializes a Write transform.

    Args:
      *args: A tuple of position arguments.
      **kwargs: A dictionary of keyword arguments.

    The *args, **kwargs are expected to be (label, sink) or (sink).
    """
    label, sink = self.parse_label_and_arg(args, kwargs, 'sink')
    super(Write, self).__init__(label)
    self.sink = sink

  def apply(self, pcoll):
    from google.cloud.dataflow.io import iobase
    if isinstance(self.sink, iobase.NativeSink):
      # A native sink
      return pcoll | _NativeWrite('native_write', self.sink)
    elif isinstance(self.sink, iobase.Sink):
      # A custom sink
      return pcoll | WriteImpl(self.sink)
    elif isinstance(self.sink, ptransform.PTransform):
      # This allows "composite" sinks to be used like non-composite ones.
      return pcoll | self.sink
    else:
      raise ValueError('A sink must inherit iobase.Sink, iobase.NativeSink, '
                       'or be a PTransform. Received : %r', self.sink)


class WriteImpl(ptransform.PTransform):
  """Implements the writing of custom sinks."""

  def __init__(self, sink):
    super(WriteImpl, self).__init__()
    self.sink = sink

  def apply(self, pcoll):
    sink_coll = pcoll.pipeline | core.Create('create_sink_collection',
                                             [self.sink])
    init_result_coll = sink_coll | core.Map(
        'initialize_write', lambda sink: sink.initialize_write())
    write_result_coll = pcoll | core.ParDo(
        'write_bundles', _WriteBundleDoFn(),
        AsSingleton(sink_coll),
        AsSingleton(init_result_coll))
    return sink_coll | core.FlatMap(
        'finalize_write',
        lambda sink, init_result, write_results:
        (window.TimestampedValue(v, window.MAX_TIMESTAMP)
         for v in sink.finalize_write(init_result, write_results) or ()),
        AsSingleton(init_result_coll),
        AsIter(write_result_coll))


class _WriteBundleDoFn(core.DoFn):
  """A DoFn for writing elements to an iobase.Writer.

  Opens a writer at the first element and closes the writer at finish_bundle().
  """

  def __init__(self):
    self.writer = None

  def process(self, context, sink, init_result):
    if self.writer is None:
      self.writer = sink.open_writer(init_result, str(uuid.uuid4()))
    self.writer.write(context.element)

  def finish_bundle(self, context, *args, **kwargs):
    if self.writer is not None:
      yield window.TimestampedValue(self.writer.close(), window.MAX_TIMESTAMP)

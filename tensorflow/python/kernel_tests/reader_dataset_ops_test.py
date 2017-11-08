# Copyright 2017 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Tests for the experimental input pipeline ops."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import gzip
import os
import zlib

from tensorflow.python.data.ops import iterator_ops
from tensorflow.python.data.ops import readers
from tensorflow.python.framework import constant_op
from tensorflow.python.framework import dtypes
from tensorflow.python.framework import errors
from tensorflow.python.lib.io import python_io
from tensorflow.python.ops import array_ops
from tensorflow.python.platform import test
from tensorflow.python.util import compat


class TextLineDatasetTest(test.TestCase):

  def _lineText(self, f, l):
    return compat.as_bytes("%d: %d" % (f, l))

  def _createFiles(self,
                   num_files,
                   num_lines,
                   crlf=False,
                   compression_type=None):
    filenames = []
    for i in range(num_files):
      fn = os.path.join(self.get_temp_dir(), "text_line.%d.txt" % i)
      filenames.append(fn)
      contents = []
      for j in range(num_lines):
        contents.append(self._lineText(i, j))
        # Always include a newline after the record unless it is
        # at the end of the file, in which case we include it
        if j + 1 != num_lines or i == 0:
          contents.append(b"\r\n" if crlf else b"\n")
      contents = b"".join(contents)

      if not compression_type:
        with open(fn, "wb") as f:
          f.write(contents)
      elif compression_type == "GZIP":
        with gzip.GzipFile(fn, "wb") as f:
          f.write(contents)
      elif compression_type == "ZLIB":
        contents = zlib.compress(contents)
        with open(fn, "wb") as f:
          f.write(contents)
      else:
        raise ValueError("Unsupported compression_type", compression_type)

    return filenames

  def _testTextLineDataset(self, compression_type=None):
    test_filenames = self._createFiles(
        2, 5, crlf=True, compression_type=compression_type)
    filenames = array_ops.placeholder(dtypes.string, shape=[None])
    num_epochs = array_ops.placeholder(dtypes.int64, shape=[])
    batch_size = array_ops.placeholder(dtypes.int64, shape=[])

    repeat_dataset = readers.TextLineDataset(
        filenames, compression_type=compression_type).repeat(num_epochs)
    batch_dataset = repeat_dataset.batch(batch_size)

    iterator = iterator_ops.Iterator.from_structure(batch_dataset.output_types)
    init_op = iterator.make_initializer(repeat_dataset)
    init_batch_op = iterator.make_initializer(batch_dataset)
    get_next = iterator.get_next()

    with self.test_session() as sess:
      # Basic test: read from file 0.
      sess.run(
          init_op, feed_dict={filenames: [test_filenames[0]],
                              num_epochs: 1})
      for i in range(5):
        self.assertEqual(self._lineText(0, i), sess.run(get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(get_next)

      # Basic test: read from file 1.
      sess.run(
          init_op, feed_dict={filenames: [test_filenames[1]],
                              num_epochs: 1})
      for i in range(5):
        self.assertEqual(self._lineText(1, i), sess.run(get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(get_next)

      # Basic test: read from both files.
      sess.run(init_op, feed_dict={filenames: test_filenames, num_epochs: 1})
      for j in range(2):
        for i in range(5):
          self.assertEqual(self._lineText(j, i), sess.run(get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(get_next)

      # Test repeated iteration through both files.
      sess.run(init_op, feed_dict={filenames: test_filenames, num_epochs: 10})
      for _ in range(10):
        for j in range(2):
          for i in range(5):
            self.assertEqual(self._lineText(j, i), sess.run(get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(get_next)

      # Test batched and repeated iteration through both files.
      sess.run(
          init_batch_op,
          feed_dict={filenames: test_filenames,
                     num_epochs: 10,
                     batch_size: 5})
      for _ in range(10):
        self.assertAllEqual([self._lineText(0, i) for i in range(5)],
                            sess.run(get_next))
        self.assertAllEqual([self._lineText(1, i) for i in range(5)],
                            sess.run(get_next))

  def testTextLineDatasetNoCompression(self):
    self._testTextLineDataset()

  def testTextLineDatasetGzipCompression(self):
    self._testTextLineDataset(compression_type="GZIP")

  def testTextLineDatasetZlibCompression(self):
    self._testTextLineDataset(compression_type="ZLIB")

  def testTextLineDatasetBuffering(self):
    test_filenames = self._createFiles(2, 5, crlf=True)

    repeat_dataset = readers.TextLineDataset(test_filenames, buffer_size=10)
    iterator = repeat_dataset.make_one_shot_iterator()

    with self.test_session() as sess:
      for j in range(2):
        for i in range(5):
          self.assertEqual(self._lineText(j, i), sess.run(iterator.get_next()))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(iterator.get_next())


class FixedLengthRecordReaderTest(test.TestCase):

  def setUp(self):
    super(FixedLengthRecordReaderTest, self).setUp()
    self._num_files = 2
    self._num_records = 7
    self._header_bytes = 5
    self._record_bytes = 3
    self._footer_bytes = 2

  def _record(self, f, r):
    return compat.as_bytes(str(f * 2 + r) * self._record_bytes)

  def _createFiles(self):
    filenames = []
    for i in range(self._num_files):
      fn = os.path.join(self.get_temp_dir(), "fixed_length_record.%d.txt" % i)
      filenames.append(fn)
      with open(fn, "wb") as f:
        f.write(b"H" * self._header_bytes)
        for j in range(self._num_records):
          f.write(self._record(i, j))
        f.write(b"F" * self._footer_bytes)
    return filenames

  def testFixedLengthRecordDataset(self):
    test_filenames = self._createFiles()
    filenames = array_ops.placeholder(dtypes.string, shape=[None])
    num_epochs = array_ops.placeholder(dtypes.int64, shape=[])
    batch_size = array_ops.placeholder(dtypes.int64, shape=[])

    repeat_dataset = (readers.FixedLengthRecordDataset(
        filenames, self._record_bytes, self._header_bytes, self._footer_bytes)
                      .repeat(num_epochs))
    batch_dataset = repeat_dataset.batch(batch_size)

    iterator = iterator_ops.Iterator.from_structure(batch_dataset.output_types)
    init_op = iterator.make_initializer(repeat_dataset)
    init_batch_op = iterator.make_initializer(batch_dataset)
    get_next = iterator.get_next()

    with self.test_session() as sess:
      # Basic test: read from file 0.
      sess.run(
          init_op, feed_dict={filenames: [test_filenames[0]],
                              num_epochs: 1})
      for i in range(self._num_records):
        self.assertEqual(self._record(0, i), sess.run(get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(get_next)

      # Basic test: read from file 1.
      sess.run(
          init_op, feed_dict={filenames: [test_filenames[1]],
                              num_epochs: 1})
      for i in range(self._num_records):
        self.assertEqual(self._record(1, i), sess.run(get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(get_next)

      # Basic test: read from both files.
      sess.run(init_op, feed_dict={filenames: test_filenames, num_epochs: 1})
      for j in range(self._num_files):
        for i in range(self._num_records):
          self.assertEqual(self._record(j, i), sess.run(get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(get_next)

      # Test repeated iteration through both files.
      sess.run(init_op, feed_dict={filenames: test_filenames, num_epochs: 10})
      for _ in range(10):
        for j in range(self._num_files):
          for i in range(self._num_records):
            self.assertEqual(self._record(j, i), sess.run(get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(get_next)

      # Test batched and repeated iteration through both files.
      sess.run(
          init_batch_op,
          feed_dict={
              filenames: test_filenames,
              num_epochs: 10,
              batch_size: self._num_records
          })
      for _ in range(10):
        for j in range(self._num_files):
          self.assertAllEqual(
              [self._record(j, i) for i in range(self._num_records)],
              sess.run(get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(get_next)

  def testFixedLengthRecordDatasetBuffering(self):
    test_filenames = self._createFiles()
    dataset = readers.FixedLengthRecordDataset(
        test_filenames,
        self._record_bytes,
        self._header_bytes,
        self._footer_bytes,
        buffer_size=10)
    iterator = dataset.make_one_shot_iterator()

    with self.test_session() as sess:
      for j in range(self._num_files):
        for i in range(self._num_records):
          self.assertEqual(self._record(j, i), sess.run(iterator.get_next()))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(iterator.get_next())


class TFRecordDatasetTest(test.TestCase):

  def setUp(self):
    super(TFRecordDatasetTest, self).setUp()
    self._num_files = 2
    self._num_records = 7

    self.test_filenames = self._createFiles()

    self.filenames = array_ops.placeholder(dtypes.string, shape=[None])
    self.num_epochs = array_ops.placeholder_with_default(
        constant_op.constant(1, dtypes.int64), shape=[])
    self.compression_type = array_ops.placeholder_with_default("", shape=[])
    self.batch_size = array_ops.placeholder(dtypes.int64, shape=[])

    repeat_dataset = readers.TFRecordDataset(self.filenames,
                                             self.compression_type).repeat(
                                                 self.num_epochs)
    batch_dataset = repeat_dataset.batch(self.batch_size)

    iterator = iterator_ops.Iterator.from_structure(batch_dataset.output_types)
    self.init_op = iterator.make_initializer(repeat_dataset)
    self.init_batch_op = iterator.make_initializer(batch_dataset)
    self.get_next = iterator.get_next()

  def _record(self, f, r):
    return compat.as_bytes("Record %d of file %d" % (r, f))

  def _createFiles(self):
    filenames = []
    for i in range(self._num_files):
      fn = os.path.join(self.get_temp_dir(), "tf_record.%d.txt" % i)
      filenames.append(fn)
      writer = python_io.TFRecordWriter(fn)
      for j in range(self._num_records):
        writer.write(self._record(i, j))
      writer.close()
    return filenames

  def testReadOneEpoch(self):
    with self.test_session() as sess:
      # Basic test: read from file 0.
      sess.run(
          self.init_op,
          feed_dict={
              self.filenames: [self.test_filenames[0]],
              self.num_epochs: 1
          })
      for i in range(self._num_records):
        self.assertAllEqual(self._record(0, i), sess.run(self.get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(self.get_next)

      # Basic test: read from file 1.
      sess.run(
          self.init_op,
          feed_dict={
              self.filenames: [self.test_filenames[1]],
              self.num_epochs: 1
          })
      for i in range(self._num_records):
        self.assertAllEqual(self._record(1, i), sess.run(self.get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(self.get_next)

      # Basic test: read from both files.
      sess.run(
          self.init_op,
          feed_dict={self.filenames: self.test_filenames,
                     self.num_epochs: 1})
      for j in range(self._num_files):
        for i in range(self._num_records):
          self.assertAllEqual(self._record(j, i), sess.run(self.get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(self.get_next)

  def testReadTenEpochs(self):
    with self.test_session() as sess:
      sess.run(
          self.init_op,
          feed_dict={self.filenames: self.test_filenames,
                     self.num_epochs: 10})
      for _ in range(10):
        for j in range(self._num_files):
          for i in range(self._num_records):
            self.assertAllEqual(self._record(j, i), sess.run(self.get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(self.get_next)

  def testReadTenEpochsOfBatches(self):
    with self.test_session() as sess:
      sess.run(
          self.init_batch_op,
          feed_dict={
              self.filenames: self.test_filenames,
              self.num_epochs: 10,
              self.batch_size: self._num_records
          })
      for _ in range(10):
        for j in range(self._num_files):
          values = sess.run(self.get_next)
          self.assertAllEqual(
              [self._record(j, i) for i in range(self._num_records)], values)
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(self.get_next)

  def testReadZlibFiles(self):
    zlib_files = []
    for i, fn in enumerate(self.test_filenames):
      with open(fn, "rb") as f:
        cdata = zlib.compress(f.read())

        zfn = os.path.join(self.get_temp_dir(), "tfrecord_%s.z" % i)
        with open(zfn, "wb") as f:
          f.write(cdata)
        zlib_files.append(zfn)

    with self.test_session() as sess:
      sess.run(
          self.init_op,
          feed_dict={self.filenames: zlib_files,
                     self.compression_type: "ZLIB"})
      for j in range(self._num_files):
        for i in range(self._num_records):
          self.assertAllEqual(self._record(j, i), sess.run(self.get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(self.get_next)

  def testReadGzipFiles(self):
    gzip_files = []
    for i, fn in enumerate(self.test_filenames):
      with open(fn, "rb") as f:
        gzfn = os.path.join(self.get_temp_dir(), "tfrecord_%s.gz" % i)
        with gzip.GzipFile(gzfn, "wb") as gzf:
          gzf.write(f.read())
        gzip_files.append(gzfn)

    with self.test_session() as sess:
      sess.run(
          self.init_op,
          feed_dict={self.filenames: gzip_files,
                     self.compression_type: "GZIP"})
      for j in range(self._num_files):
        for i in range(self._num_records):
          self.assertAllEqual(self._record(j, i), sess.run(self.get_next))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(self.get_next)

  def testReadWithBuffer(self):
    one_mebibyte = 2**20
    d = readers.TFRecordDataset(self.test_filenames, buffer_size=one_mebibyte)
    iterator = d.make_one_shot_iterator()
    with self.test_session() as sess:
      for j in range(self._num_files):
        for i in range(self._num_records):
          self.assertAllEqual(self._record(j, i), sess.run(iterator.get_next()))
      with self.assertRaises(errors.OutOfRangeError):
        sess.run(iterator.get_next())


if __name__ == "__main__":
  test.main()

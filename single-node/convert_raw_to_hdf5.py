#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2018 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: EPL-2.0
#

"""
Converts the Medical Decathlon raw Nifti files into
single HDF5 file for easier use in TensorFlow/Keras.

You'll need to download the raw dataset from
the Medical Decathlon website (http://medicaldecathlon.com),
extract the data (untar), and run this script.

The raw dataset has the CC-BY-SA 4.0 license.
https://creativecommons.org/licenses/by-sa/4.0/

"""

import os
import nibabel as nib  # pip install nibabel
import numpy as np
from tqdm import tqdm  # pip install tqdm
import h5py   # pip install h5py
import json

import argparse

parser = argparse.ArgumentParser(
    description="Convert Decathlon raw Nifti data "
    "(http://medicaldecathlon.com) "
    "files to Numpy data files",
    add_help=True, formatter_class=argparse.ArgumentDefaultsHelpFormatter)

parser.add_argument("--data_path",
                    default="../../data/decathlon/Task01_BrainTumour/",
                    help="Path to the raw BraTS datafiles")
parser.add_argument("--save_path",
                    default="../../data/decathlon/",
                    help="Folder to save Numpy data files")
parser.add_argument("--output_filename",
                    default="decathlon_brats.h5",
                    help="Name of the output HDF5 file")
parser.add_argument("--resize", type=int, default=144,
                    help="Resize height and width to this size. "
                    "Original size = 240")
parser.add_argument("--split", type=float, default=0.85,
                    help="Train/test split ratio")

args = parser.parse_args()


def crop_center(img, cropx, cropy, cropz):
    """
    Take a center crop of the images.
    If we are using a 2D model, then we'll just stack the
    z dimension.
    """

    if len(img.shape) == 4:
        x, y, z, c = img.shape
    else:
        x, y, z = img.shape

    # Make sure starting index is >= 0
    startx = max(x//2-(cropx//2), 0)
    starty = max(y//2-(cropy//2), 0)
    startz = max(z//2-(cropz//2), 0)

    # Make sure ending index is <= size
    endx = min(startx + cropx, x)
    endy = min(starty + cropy, y)
    endz = min(startz + cropz, z)

    if len(img.shape) == 4:
        return img[startx:endx, starty:endy, startz:endz, :]
    else:
        return img[startx:endx, starty:endy, startz:endz]


def normalize_img(img):
    """
    Normalize the pixel values.
    This is one of the most important preprocessing steps.
    We need to make sure that the pixel values have a mean of 0
    and a standard deviation of 1 to help the model to train
    faster and more accurately.
    """

    for channel in range(img.shape[3]):
        img[:, :, :, channel] = (
            img[:, :, :, channel] - np.mean(img[:, :, :, channel])) \
            / np.std(img[:, :, :, channel])

    return img

def attach_attributes(df, json_data, name):
    """
    Save the json data
    """

    if type(json_data) is str:
        length = 1
    else:
        length = len(json_data)

    dt = h5py.special_dtype(vlen=str)
    dset = df.create_dataset(name, (length,), dtype=dt)
    dset[:] = json_data

def preprocess_inputs(img):
    """
    Process the input images
    """
    img = crop_center(img, args.resize, args.resize, args.resize)
    img = normalize_img(img)

    img = np.swapaxes(np.array(img), 0, -2)

    return img

def preprocess_labels(msk):
    """
    Process the ground truth labels
    """

    msk = crop_center(msk, args.resize, args.resize, args.resize)

    msk[msk > 1] = 1  # Combine all masks
    msk = np.expand_dims(np.swapaxes(np.array(msk), 0, -1), -1)

    return msk

def convert_raw_data_to_hdf5(trainIdx, validateIdx, fileIdx,
                             filename, dataDir, json_data):
    """
    Go through the Decathlon dataset.json file.
    We've already split into training and validation subsets.
    Read in Nifti format files. Crop images and masks.
    Save to HDF5 format.
    This code is will convert the 3D images and masks
    into a stack of 2D slices.
    """
    hdf_file = h5py.File(filename, "w")

    # Save the dataset attributes
    attach_attributes(hdf_file, str(json_data["modality"]), "modalities")
    attach_attributes(hdf_file, json_data["licence"], "license")
    attach_attributes(hdf_file, json_data["reference"], "reference")
    attach_attributes(hdf_file, json_data["name"], "name")
    attach_attributes(hdf_file, json_data["description"], "description")
    attach_attributes(hdf_file, json_data["release"], "release")

    # Training filenames
    train_image_files = []
    train_label_files = []
    for idx in trainIdx:
        train_image_files.append(fileIdx[idx]["image"])
        train_label_files.append(fileIdx[idx]["label"])

    # Validation filenames
    validate_image_files = []
    validate_label_files = []
    for idx in validateIdx:
        validate_image_files.append(fileIdx[idx]["image"])
        validate_label_files.append(fileIdx[idx]["label"])

    attach_attributes(hdf_file, train_image_files, "training_input_files")
    attach_attributes(hdf_file, train_label_files, "training_label_files")
    attach_attributes(hdf_file, validate_image_files, "validation_input_files")
    attach_attributes(hdf_file, validate_label_files, "validation_label_files")

    # Save training set images
    print("Step 1 of 4. Save training set images.")
    first = True
    for idx in tqdm(train_image_files):

        data_filename = os.path.join(dataDir, idx)
        img = np.array(nib.load(data_filename).dataobj)
        img = preprocess_inputs(img)
        num_rows = img.shape[0]

        if first:
            first = False
            img_train_dset = hdf_file.create_dataset("imgs_train",
                                                     img.shape,
                                                     maxshape=(None, img.shape[1],
                                                               img.shape[2], img.shape[3]),
                                                     dtype=float, compression="gzip")
            img_train_dset[:] = img
        else:
            row = img_train_dset.shape[0]  # Count current dataset rows
            img_train_dset.resize(row+num_rows, axis=0)  # Add new row
            # Insert data into new row
            img_train_dset[row:(row+num_rows), :] = img


    # Save validation set images
    print("Step 2 of 4. Save validation set images.")
    first = True
    for idx in tqdm(validate_image_files):

        # Nibabel should read the file as X,Y,Z,C
        data_filename = os.path.join(dataDir, idx)
        img = np.array(nib.load(data_filename).dataobj)
        img = preprocess_inputs(img)

        num_rows = img.shape[0]

        if first:
            first = False
            img_validation_dset = hdf_file.create_dataset("imgs_validation",
                                                          img.shape,
                                                          maxshape=(None, img.shape[1],
                                                                    img.shape[2], img.shape[3]),
                                                          dtype=float, compression="gzip")
            img_validation_dset[:] = img
        else:
            row = img_validation_dset.shape[0]  # Count current dataset rows
            img_validation_dset.resize(row+num_rows, axis=0)  # Add new row
            # Insert data into new row
            img_validation_dset[row:(row+num_rows), :] = img


    # Save training set masks
    print("Step 3 of 4. Save training set masks.")
    first = True
    for idx in tqdm(train_label_files):

        data_filename = os.path.join(dataDir, idx)
        msk = np.array(nib.load(data_filename).dataobj)
        msk = preprocess_labels(msk)
        num_rows = msk.shape[0]

        if first:
            first = False
            msk_train_dset = hdf_file.create_dataset("msks_train",
                                                     msk.shape,
                                                     maxshape=(None, msk.shape[1],
                                                               msk.shape[2], msk.shape[3]),
                                                     dtype=float, compression="gzip")
            msk_train_dset[:] = msk
        else:
            row = msk_train_dset.shape[0]  # Count current dataset rows
            msk_train_dset.resize(row+num_rows, axis=0)  # Add new row
            # Insert data into new row
            msk_train_dset[row:(row+num_rows), :] = msk


    # Save testing/validation set masks

    print("Step 4 of 4. Save validation set masks.")
    first = True
    for idx in tqdm(validate_label_files):

        data_filename = os.path.join(dataDir, idx)
        msk = np.array(nib.load(data_filename).dataobj)
        msk = preprocess_labels(msk)

        num_rows = msk.shape[0]

        if first:
            first = False
            msk_validation_dset = hdf_file.create_dataset("msks_validation",
                                                          msk.shape,
                                                          maxshape=(None, msk.shape[1],
                                                                    msk.shape[2], msk.shape[3]),
                                                          dtype=float, compression="gzip")
            msk_validation_dset[:] = msk
        else:
            row = msk_validation_dset.shape[0]  # Count current dataset rows
            msk_validation_dset.resize(row+num_rows, axis=0)  # Add new row
            # Insert data into new row
            msk_validation_dset[row:(row+num_rows), :] = msk


    hdf_file.close()
    print("Finished processing.")
    print("HDF5 saved to {}".format(filename))


if __name__ == "__main__":

    print("Converting Decathlon raw Nifti data files to single "
          "training and validation HDF5 data file.")
    print(args)

    save_dir = os.path.join(
        args.save_path, "{}x{}/".format(args.resize, args.resize))

    # Create directory
    try:
        os.makedirs(save_dir)
    except OSError:
        if not os.path.isdir(save_dir):
            raise

    filename = os.path.join(save_dir, args.output_filename)
    # Check for existing output file and delete if exists
    if os.path.exists(filename):
        print("Removing existing data file: {}".format(filename))
        os.remove(filename)

    """
    Get the training file names from the data directory.
    Decathlon should always have a dataset.json file in the
    subdirectory which lists the experiment information including
    the input and label filenames.
    """

    json_filename = os.path.join(args.data_path, "dataset.json")

    try:
        with open(json_filename, "r") as fp:
            experiment_data = json.load(fp)
    except IOError as e:
        print("File {} doesn't exist. It should be part of the "
              "Decathlon directory".format(json_filename))

    # Print information about the Decathlon experiment data
    print("*"*30)
    print("="*30)
    print("Dataset name:        ", experiment_data["name"])
    print("Dataset description: ", experiment_data["description"])
    print("Dataset release:     ", experiment_data["release"])
    print("Dataset reference:   ", experiment_data["reference"])
    print("Dataset license:     ", experiment_data["licence"])  # sic
    print("="*30)
    print("*"*30)

    """
    Randomize the file list. Then separate into training and
    validation (testing) lists.
    """
    # Set the random seed so that always get same random mix
    np.random.seed(816)
    numFiles = experiment_data["numTraining"]
    idxList = np.arange(numFiles)  # List of file indices
    randomList = np.random.random((numFiles))  # List of random numbers
    # Random number go from 0 to 1. So anything above
    # args.train_split is in the validation list.
    trainList = idxList[randomList < args.split]
    validateList = idxList[randomList >= args.split]

    convert_raw_data_to_hdf5(trainList, validateList,
                             experiment_data["training"],
                             filename, args.data_path,
                             experiment_data)

#!/usr/bin/env python
"""
 Copyright (c) 2018 Intel Corporation

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""


import sys
import os
from argparse import ArgumentParser
import numpy as np
import logging as log
from time import time
import h5py
from openvino.inference_engine import IENetwork, IEPlugin

imgs_to_use = [61]

def dice_score(pred, truth):
    """
    Sorensen Dice score
    Measure of the overlap between the prediction and ground truth masks
    """
    numerator = np.sum(pred * truth) * 2.0 + 1.0
    denominator = np.sum(pred) + np.sum(truth) + 1.0

    return numerator / denominator

def evaluate_model(res, input_data, label_data, args):
    """
    Evaluate the model results
    """

    import matplotlib.pyplot as plt

    # Processing output blob
    log.info("Processing U-Net model")
    idx = 0
    
    for batch, prediction in enumerate(res):

        dice = dice_score(prediction, label_data[idx,0,:,:])
        log.info("{}: Dice score = {:.4f}".format(imgs_to_use[idx], dice))

        if args.plot:
            if idx==0:  plt.figure(figsize=(10,10))
            plt.subplot(args.batch_size, 3, 1+idx*3)
            plt.imshow(prediction[0])
            if idx==0:  plt.title("Prediction")
            plt.subplot(args.batch_size, 3, 2+idx*3)
            plt.imshow(label_data[idx,0,:,:])
            if idx==0: plt.title("Ground truth")
            plt.subplot(args.batch_size, 3, 3+idx*3)
            plt.imshow(input_data[idx,0,:,:], cmap="bone")
            if idx==0: plt.title("MRI")

        idx += 1

    if args.plot:
        plt.suptitle("U-Net Model Predictions")
        plt.savefig("output.png")

def load_data(batch_size):
    """
    Modify this to load your data and labels
    """

    df = h5py.File("../../../data/decathlon/144x144/Task01_BrainTumour.h5", "r")

    input_data = df["imgs_validation"][imgs_to_use,]
    msks_data = df["msks_validation"][imgs_to_use,]

    input_data = input_data.transpose((0,3,1,2))
    msks_data = msks_data.transpose((0,3,1,2))

    log.info("Batch size is {} images.".format(batch_size))

    return input_data, msks_data

def load_model(fp16=False):
    """
    Load the OpenVINO model.
    """
    log.info("Loading U-Net model to the plugin")

    if fp16:  # Floating point 16 is for Myriad X
        model_xml = "./FP16/saved_model.xml"
    else:     # FP32 for most devices
        model_xml = "./FP32/saved_model.xml"

    model_bin = os.path.splitext(model_xml)[0] + ".bin"

    return model_xml, model_bin


def build_argparser():
    parser = ArgumentParser()
    parser.add_argument("-bz", "--batch_size",
                        help="Batch size", default=1, type=int)
    parser.add_argument("-number_iter", "--number_iter",
                        help="Number of iterations", default=5, type=int)
    parser.add_argument("-perf_counts", "--perf_counts",
                        help="Performance counts", default=1, type=int)
    parser.add_argument("-l", "--cpu_extension",
                        help="MKLDNN (CPU)-targeted custom layers. "
                             "Absolute path to a shared library with "
                             "the kernels impl.", type=str)
    parser.add_argument("-pp", "--plugin_dir", help="Path to a plugin folder",
                        type=str, default=None)
    parser.add_argument("-d", "--device",
                        help="Specify the target device to infer on; "
                             "CPU, GPU, FPGA or MYRIAD is acceptable. Sample "
                             "will look for a suitable plugin for device "
                             "specified (CPU by default)", default="CPU",
                        type=str)
    parser.add_argument("-plot", "--plot", help="Plot results",
                        default=False, action="store_true")
    return parser

def main():

    log.basicConfig(format="[ %(levelname)s ] %(message)s",
                    level=log.INFO, stream=sys.stdout)
    args = build_argparser().parse_args()

    # Plugin initialization for specified device and
    #     load extensions library if specified
    plugin = IEPlugin(device=args.device, plugin_dirs=args.plugin_dir)
    if args.cpu_extension and 'CPU' in args.device:
        plugin.add_cpu_extension(args.cpu_extension)

    # Read IR
    # If using MYRIAD then we need to load FP16 model version
    model_xml, model_bin = load_model(args.device == "MYRIAD")

    log.info("Loading network files:\n\t{}\n\t{}".format(model_xml, model_bin))
    net = IENetwork(model=model_xml, weights=model_bin)

    if "CPU" in plugin.device:
        supported_layers = plugin.get_supported_layers(net)
        not_supported_layers = [l for l in net.layers.keys() if l not in supported_layers]
        if len(not_supported_layers) != 0:
            log.error("Following layers are not supported by the plugin "
                      " for specified device {}:\n {}".
                      format(plugin.device, ", ".join(not_supported_layers)))
            log.error("Please try to specify cpu extensions library path "
                      "in sample's command line parameters using -l "
                      "or --cpu_extension command line argument")
            sys.exit(1)

    assert len(net.inputs.keys()) == 1, "Sample supports only single input topologies"
    assert len(net.outputs) == 1, "Sample supports only single output topologies"

    log.info("Preparing input blobs")
    input_blob = next(iter(net.inputs))
    out_blob = next(iter(net.outputs))

    net.batch_size = args.batch_size

    # Load data
    input_data, label_data = load_data(args.batch_size)

    # Loading model to the plugin
    exec_net = plugin.load(network=net)
    del net

    # Start sync inference
    log.info("Starting inference ({} iterations)".format(args.number_iter))
    infer_time = []
    for i in range(args.number_iter):
        t0 = time()
        res = exec_net.infer(inputs={input_blob: input_data})
        infer_time.append((time() - t0) * 1000)

    average_inference = np.average(np.asarray(infer_time))
    log.info("Average running time of one batch: {:.5f} ms".format(average_inference))
    log.info("Images per second = {:.3f}".format(args.batch_size * 1000.0 / average_inference))
    if args.perf_counts:
        perf_counts = exec_net.requests[0].get_perf_counts()
        log.info("Performance counters:")
        log.info("{:<70} {:<15} {:<15} {:<15} {:<10}".format("name",
                                                             "layer_type",
                                                             "exec_type",
                                                             "status",
                                                             "real_time, us"))
        for layer, stats in perf_counts.items():
            log.info("{:<70} {:<15} {:<15} {:<15} {:<10}".format(layer,
                                                                 stats["layer_type"],
                                                                 stats["exec_type"],
                                                                 stats["status"],
                                                                 stats["real_time"]))

    res_out = res[out_blob]
    evaluate_model(res_out, input_data, label_data, args)

    del exec_net
    del plugin

if __name__ == '__main__':
    sys.exit(main() or 0)

# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

# pylint: skip-file
# flake8: noqa

from hyperopt import hp


TopkAmountStrategySpace = {
    "topk": hp.choice("topk", [30, 35, 40]),
    "buffer_margin": hp.choice("buffer_margin", [200, 250, 300]),
}

TopkDropoutStrategySpace = {
    "topk": hp.choice("topk", [30, 40, 50, 60]),
    "n_drop": hp.choice("n_drop", [2, 5]),
}

LGBModelSpace = {
    "learning_rate": hp.choice("learning_rate", [0.01, 0.02, 0.05, 0.1, 0.2]),
    "num_leaves": hp.choice("num_leaves", [31, 63, 127, 255]),
    "max_depth": hp.choice("max_depth", [4, 6, 8, 10]),
    "subsample": hp.uniform("subsample", 0.6, 1.0),
    "colsample_bytree": hp.uniform("colsample_bytree", 0.6, 1.0),
}

QLibDataLabelSpace = {
    "labels": hp.choice(
        "labels",
        [["Ref($vwap, -2)/Ref($vwap, -1) - 1"], ["Ref($close, -5)/$close - 1"]],
    )
}

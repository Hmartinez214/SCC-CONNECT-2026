#!/bin/bash

cd $CLONE_DIR

git clone https://github.com/namehta4/ValQyriE.git
cd ValQriE

pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e ".[gqe,test]"
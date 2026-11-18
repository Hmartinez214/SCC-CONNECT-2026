#!/bin/bash

#Stress info
python -m stress info >> outputs/valrqrie/stress_info.txt
python -m stress peak >> outputs/valrqrie/stress_peak.txt


python -m bench run h2o-24q --point 2


py-spy record -f raw -o outputs/valqyrie/profile.txt -- python -m bench run h2o-24q --point 2
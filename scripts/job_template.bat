#!/bin/bash
########### EDIT INFO ###############
#SBATCH -J <NAME>
#SBATCH -N <NODES>
#SBATCH -n <NTASKS>
#SBATCH --output=outputs/slurm/job_%j.out
#SBATCH --error=outputs/slurm/job_%j.err

#Job information
RUN_GROUP="$SLURM_JOB_NAME" # Folder which outputs will be stored
DESCRIPTION="<DESCRIPTION>"
#####################################

# File organization section
RUN_ROOT="${SLURM_SUBMIT_DIR}"
OUTPUT=$RUN_ROOT/outputs                        #Output folder
OUTDIR=$OUTPUT/${RUN_GROUP}                        #Run group folder
OUTFILE="${OUTDIR}/${RUN_GROUP}_${SLURM_JOB_ID}.log"    #Run specific output
mkdir -p $OUTDIR

# Add description to job list
echo "$SLURM_JOB_ID : $DESCRIPTION - Using $SLURM_NTASKS tasks and $SLURM_JOB_NUM_NODES nodes" >> $OUTPUT/descriptions.txt

###### START OF RUN ########
echo "Started running $RUN_GROUP at $(date)" | tee -a $OUTFILE
start_time=$(date +%s)

################
# < INSERT YOUR SCRIPT HERE >
# Use | tee -a $OUTFILE, to clean up outputs
################

echo "Finished running $RUN_GROUP at $(date)" | tee -a $OUTFILE
end_time=$(date +%s)
elapsed=$((end_time - start_time))
echo "Elapsed time: $elapsed seconds" | tee -a $OUTFILE
###### END OF RUN ########
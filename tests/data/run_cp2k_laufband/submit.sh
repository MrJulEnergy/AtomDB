#!/bin/bash
#SBATCH --job-name=kcl_r2scan_label
#SBATCH --partition=cpu
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=64
#SBATCH --cpus-per-task=1
#SBATCH --time=24:00:00
#SBATCH --mem=200G
#SBATCH -A icp_kcl
module load cp2k/2025.1
export OMP_NUM_THREADS=1
# many jobs of this script run concurrently; laufband hands out frames
srun python label.py --input structures.extxyz --output labeled.h5

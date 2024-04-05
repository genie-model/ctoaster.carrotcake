#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

#Permission for running this script
chmod +x run_sequence.sh

# Remove the .ctoasterrc file from the home directory
rm -rf ~/.ctoasterrc

# Execute the setup script for ctoaster
./setup-ctoaster

# Run basic tests
./tests run basic

# Start a new job with specified parameters
./new-job -b cgenie.eb_go_gs_ac_bg.p0650e.NONE -u LABS/LAB_0.snowball snowball 10

# Change directory to the snowball jobs directory
cd ~/ctoaster.cupcake-jobs/snowball

# Execute the go run command
./go run

# Clean up after the run
./go clean

# Change directory to the ctoaster.cupcake directory
cd ../../ctoaster.cupcake

# Run the cupcake with specified parameters
./run-cupcake cgenie.eb_go_gs_ac_bg.p0650e.NONE LABS LAB_0.snowball 10

# Execute the coverage command with the basic parameter
./coverage basic

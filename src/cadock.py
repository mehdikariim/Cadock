import os
import subprocess
import sys
from rdkit import Chem
from rdkit.Chem import AllChem
from Bio.PDB import PDBList
import pandas as pd
import numpy as np
from pymol import cmd
from IPython.display import Image, display

def generate_minimized_pdb(smiles, pdb_filename):
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        print(f"Invalid SMILES string: {smiles}")
        return False
    mol = Chem.AddHs(mol)
    try:
        AllChem.EmbedMolecule(mol, randomSeed=42)
    except:
        print(f"Failed to generate 3D coordinates for SMILES: {smiles}")
        return False
    try:
        AllChem.UFFOptimizeMolecule(mol, maxIters=200)
    except:
        print(f"Energy minimization failed for SMILES: {smiles}")
        return False
    try:
        Chem.SanitizeMol(mol)
    except:
        print(f"Sanitization failed for SMILES: {smiles}")
        return False
    Chem.MolToPDBFile(mol, pdb_filename)
    print(f"Minimized molecule saved as {pdb_filename}")
    return True

def download_pdb(pdb_id, download_dir):
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)
    pdbl = PDBList()
    pdb_file_path = pdbl.retrieve_pdb_file(pdb_id, file_format='pdb', pdir=download_dir)
    return pdb_file_path

def remove_hetatm(input_pdb, output_pdb):
    with open(input_pdb, 'r') as infile, open(output_pdb, 'w') as outfile:
        for line in infile:
            if not line.startswith('HETATM'):
                outfile.write(line)

def convert_pdb_to_pdbqt_receptor(input_pdb, output_pdbqt):
    subprocess.run(['obabel', '-i', 'pdb', input_pdb, '-o', 'pdbqt', '-O', output_pdbqt, '-xr', '-xn', '-xp'], check=True)

def convert_pdb_to_pdbqt_ligand(input_pdb, output_pdbqt):
    subprocess.run(['obabel', '-i', 'pdb', input_pdb, '-o', 'pdbqt', '-O', output_pdbqt, '-h'], check=True)

def run_command_with_output(command, log_file):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    with open(log_file, 'w') as log:
        for line in process.stdout:
            sys.stdout.write(line)
            log.write(line)
            sys.stdout.flush()
    return process.wait()

def perform_docking(smiles_list, PDB_ID):
    folder_name = 'docking_results'
    receptor_name = PDB_ID

    # Create the folder
    if not os.path.exists(folder_name):
        os.mkdir(folder_name)

    print(f"Receptor Name: {receptor_name}")
    print(f"Number of ligands: {len(smiles_list)}")

    # Generate and Pre-process Ligands
    valid_smiles = []
    for i, smiles in enumerate(smiles_list):
        pdb_filename = f'{folder_name}/ligand_{i+1}.pdb'
        if generate_minimized_pdb(smiles, pdb_filename):
            valid_smiles.append(smiles)
        else:
            print(f"Skipping invalid SMILES: {smiles}")

    print(f"Number of valid SMILES processed: {len(valid_smiles)}")

    # Download and Pre-process Receptor
    downloaded_pdb_path = download_pdb(PDB_ID, folder_name)
    os.rename(downloaded_pdb_path, f'{folder_name}/{receptor_name}_dirty.pdb')

    # Remove HETATM from PDB file
    remove_hetatm(f'{folder_name}/{receptor_name}_dirty.pdb', f'{folder_name}/{receptor_name}.pdb')

    # Define Box (p2rank)
    p2rank_prank_path = os.path.join(os.getcwd(), 'p2rank_2.4.2', 'prank')
    
    # Ensure the prank script is executable
    if not os.path.isfile(p2rank_prank_path):
        raise FileNotFoundError(f"prank script not found at {p2rank_prank_path}")
    if not os.access(p2rank_prank_path, os.X_OK):
        os.chmod(p2rank_prank_path, 0o755)

    # Run p2rank to predict binding pockets
    subprocess.run([p2rank_prank_path, 'predict', '-f', f'{folder_name}/{receptor_name}.pdb'], check=True)

    # Read p2rank output
    df = pd.read_csv(f'p2rank_2.4.2/test_output/predict_{receptor_name}/{receptor_name}.pdb_predictions.csv')
    center_x, center_y, center_z = float(df['   center_x'].iloc[0]), float(df['   center_y'].iloc[0]), float(df['   center_z'].iloc[0])

    receptor_pdb = f"{folder_name}/{receptor_name}.pdb"
    receptor_pdbqt = f"{folder_name}/{receptor_name}.pdbqt"

    print("Converting receptor to PDBQT format...")
    convert_pdb_to_pdbqt_receptor(receptor_pdb, receptor_pdbqt)
    print("Receptor conversion complete.")

    results_file = f"{folder_name}/docking_results.txt"
    with open(results_file, 'w') as f:
        f.write("SMILES,Docking Score\n")  # Write header

        for i, smiles in enumerate(smiles_list):
            print(f"\nProcessing ligand {i+1} of {len(smiles_list)}")
            print(f"SMILES: {smiles}")

            ligand_pdb = f"{folder_name}/ligand_{i+1}.pdb"
            ligand_pdbqt = f"{folder_name}/ligand_{i+1}.pdbqt"

            print("Converting ligand to PDBQT format...")
            convert_pdb_to_pdbqt_ligand(ligand_pdb, ligand_pdbqt)
            print("Ligand conversion complete.")

            output = f"{folder_name}/ligand_{i+1}_out.pdbqt"
            log_file = f"{folder_name}/vina_log_{i+1}.txt"
            vina_command = [
                'vina',
                '--receptor', receptor_pdbqt,
                '--ligand', ligand_pdbqt,
                '--out', output,
                '--center_x', str(center_x),
                '--center_y', str(center_y),
                '--center_z', str(center_z),
                '--size_x', '20',
                '--size_y', '20',
                '--size_z', '20'
            ]

            print("Starting Vina docking...")
            exit_code = run_command_with_output(vina_command, log_file)

            if exit_code == 0:
                print("Vina docking completed successfully.")
                with open(log_file, 'r') as log:
                    score = "N/A"
                    for line in log:
                        if line.startswith('   1'):
                            score = line.split()[1]
                            break
                print(f"Best docking score: {score}")
            else:
                print(f"Error running Vina for ligand {i+1}. Check the log file for details.")
                score = "Error"

            # Write result to file
            f.write(f"{smiles},{score}\n")
                        # Combine receptor and ligand into a single PDB file
            cmd.reinitialize()
            cmd.load(receptor_pdb)
            cmd.load(f'{folder_name}/ligand_{i+1}_out.pdbqt')
            cmd.save(f"{folder_name}/{receptor_name}_ligand_{i+1}_best.pdb")
            print(f"Generated {receptor_name}_ligand_{i+1}_best.pdb")

def visualize_results(smiles_list, receptor_name, folder_name):
    receptor_pdb = f"{folder_name}/{receptor_name}.pdb"

    # Visualize Results
    for i in range(len(smiles_list)):
        cmd.reinitialize()
        output = f"{receptor_name}_ligand_{i+1}.pdbqt"
        cmd.load(f'{folder_name}/{output}')
        cmd.load(receptor_pdb)
        cmd.show('cartoon')
        cmd.color('cyan', 'all')
        cmd.color('red', f"{output.split('.')[0]}")
        cmd.set('ray_trace_frames', 1)
        output_image_path = f'{folder_name}/{receptor_name}_ligand_{i+1}_image.png'
        cmd.png(output_image_path)
        display(Image(output_image_path))

    # Alignment
    for i in range(len(smiles_list)):
        cmd.reinitialize()
        cmd.load(f'{folder_name}/{receptor_name}_ligand_{i+1}_best.pdb', 'docking')
        cmd.color('cyan', 'docking')
        cmd.load(f'{folder_name}/{receptor_name}_dirty.pdb', 'RealStructure')
        cmd.color('green', 'RealStructure')
        cmd.align('docking', 'RealStructure')
        cmd.show('cartoon')
        output_image_path = f'{folder_name}/alignment_ligand_{i+1}.png'
        cmd.png(output_image_path)
        display(Image(output_image_path))

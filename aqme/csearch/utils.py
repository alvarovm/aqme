#####################################################.
#            This file stores functions             #
#                 used in CSEARCH                   #
#####################################################.

import os
import sys
import subprocess
import pandas as pd
import ast
from rdkit.Chem import AllChem as Chem
from rdkit.ML.Cluster import Butina
from aqme.utils import (
    get_info_input,
    mol_from_sdf_or_mol_or_mol2,
    read_xyz_charge_mult,
    add_prefix_suffix,
    get_conf_RMS
)
from aqme.csearch.crest import nci_ts_mol


def creation_of_dup_csv_csearch(program):
    """
    Generates a pandas.DataFrame object with the appropiate columns for the
    conformational search and the minimization.

    Parameters
    ----------
    csearch : str
        Conformational search method. Current valid methods are: ['rdkit','fullmonte','summ']

    Returns
    -------
    pandas.DataFrame
    """

    # Boolean aliases from args
    is_rdkit = program == "rdkit"
    is_fullmonte = program == "fullmonte"
    is_crest = program == "crest"
    is_summ = program == "summ"

    # column blocks definitions
    base_columns = [
        "Molecule",
        "RDKit-Initial-samples",
        "RDKit-energy-window",
        "RDKit-initial_energy_threshold",
        "RDKit-RMSD-and-energy-duplicates",
        "RDKit-Unique-conformers",
    ]
    end_columns_no_min = ["CSEARCH time (seconds)"]
    fullmonte_columns = [
        "FullMonte-Unique-conformers",
    ]
    #'FullMonte-conformers',
    #'FullMonte-energy-window',
    #'FullMonte-initial_energy_threshold',
    #'FullMonte-RMSD-and-energy-duplicates']
    summ_columns = [
        "summ-conformers",
        "summ-energy-window",
        "summ-initial_energy_threshold",
        "summ-RMSD-and-energy-duplicates",
        "summ-Unique-conformers",
    ]
    crest_columns = ["Molecule", "crest-conformers"]

    # Check Conformer Search method
    if is_rdkit:
        columns = base_columns
    elif is_fullmonte:
        columns = base_columns + fullmonte_columns
    elif is_summ:
        columns = base_columns + summ_columns
    elif is_crest:
        columns = crest_columns
    else:
        return None
    columns += end_columns_no_min
    return pd.DataFrame(columns=columns)


def constraint_fix(
    constraints_atoms, constraints_dist, constraints_angle, constraints_dihedral
):
    
    # this function avoids problems when reading contraints from CSV files
    constraints_atoms = constaint_2_list(constraints_atoms)
    constraints_dist = constaint_2_list(constraints_dist)
    constraints_angle = constaint_2_list(constraints_angle)
    constraints_dihedral = constaint_2_list(constraints_dihedral)

    return constraints_atoms, constraints_dist, constraints_angle, constraints_dihedral


def constaint_2_list(contraints):
    try:
        if pd.isnull(contraints):
            contraints = []
    except ValueError:
        pass
    if not isinstance(contraints, list):
        contraints = ast.literal_eval(contraints)
    
    return contraints


def prepare_direct_smi(args):
    job_inputs = []

    if args.name is not None:
        name = args.name
        name = add_prefix_suffix(name, args)
    else:
        args.log.write(f"\nx  Specify a name ('name' option) when using the 'smi' option!")
        args.log.finalize()
        sys.exit()

    obj = (
        args.smi,
        name,
        args.charge,
        args.mult,
        args.constraints_atoms,
        args.constraints_dist,
        args.constraints_angle,
        args.constraints_dihedral,
    )
    job_inputs.append(obj)

    return job_inputs


def prepare_smiles_files(args, csearch_file):
    with open(csearch_file) as smifile:
        lines = [line for line in smifile if line.strip()]
    job_inputs = []
    for _, line in enumerate(lines):
        (
            smi,
            name,
        ) = prepare_smiles_from_line(line, args)
        obj = (
            smi,
            name,
            args.charge,
            args.mult,
            args.constraints_atoms,
            args.constraints_dist,
            args.constraints_angle,
            args.constraints_dihedral,
        )
        job_inputs.append(obj)

    return job_inputs


def prepare_smiles_from_line(line, args):

    toks = line.split()
    # editing part
    smiles = toks[0]
    name = toks[1]
    name = add_prefix_suffix(name, args)

    return smiles, name


def prepare_csv_files(args, csearch_file):
    csv_smiles = pd.read_csv(csearch_file)
    job_inputs = []
    for i in range(len(csv_smiles)):
        obj = generate_mol_from_csv(args, csv_smiles, i)
        job_inputs.append(obj)
    return job_inputs


def generate_mol_from_csv(args, csv_smiles, index):
    # assigning names and smi in each loop
    try:
        smiles = csv_smiles.loc[index, "SMILES"]
    except KeyError:
        try:
            smiles = csv_smiles.loc[index, "smiles"]
        except KeyError:
            args.log.write("\nx  Make sure the CSV file contains a column called 'SMILES' or 'smiles' with the SMILES of the molecules!")
            args.log.finalize()
            sys.exit()

    try:
        name = csv_smiles.loc[index, "code_name"]
        name = add_prefix_suffix(name, args)

    except KeyError:
        args.log.write("\nx  Make sure the CSV file contains a column called 'code_name' with the names of the molecules!")
        args.log.finalize()
        sys.exit()

    constraints_atoms = args.constraints_atoms
    constraints_dist = args.constraints_dist
    constraints_angle = args.constraints_angle
    constraints_dihedral = args.constraints_dihedral

    if "constraints_atoms" in csv_smiles.columns:
        constraints_atoms = csv_smiles.loc[index, "constraints_atoms"]

    if "constraints_dist" in csv_smiles.columns:
        constraints_dist = csv_smiles.loc[index, "constraints_dist"]

    if "constraints_angle" in csv_smiles.columns:
        constraints_angle = csv_smiles.loc[index, "constraints_angle"]

    if "constraints_dihedral" in csv_smiles.columns:
        constraints_dihedral = csv_smiles.loc[index, "constraints_dihedral"]

    (
        constraints_atoms,
        constraints_dist,
        constraints_angle,
        constraints_dihedral,
    ) = constraint_fix(
        constraints_atoms,
        constraints_dist,
        constraints_angle,
        constraints_dihedral,
    )

    charge_found, mult_found = False, False
    if args.charge is None or args.mult is None:
        for csv_column in csv_smiles.columns:
            if str(csv_smiles.loc[index, csv_column]) != 'nan':
                if csv_column.lower() in ['charge','chrg'] and args.charge is None:
                    charge = int(csv_smiles.loc[index, csv_column])
                    charge_found = True
                elif csv_column.lower() in ['mult','multiplicity'] and args.mult is None:
                    mult = int(csv_smiles.loc[index, csv_column])
                    mult_found = True

    if not charge_found:
        charge = args.charge
    if not mult_found:
        mult = args.mult

    obj = (
        smiles,
        name,
        charge,
        mult,
        constraints_atoms,
        constraints_dist,
        constraints_angle,
        constraints_dihedral,
    )

    return obj


def prepare_cdx_files(args, csearch_file):
    # converting to smiles from chemdraw
    molecules = generate_mol_from_cdx(csearch_file)

    job_inputs = []
    for i, (smiles, _) in enumerate(molecules):
        name = f"{os.path.basename(csearch_file).split('.')[0]}_{str(i)}"
        name = add_prefix_suffix(name, args)

        obj = (
            smiles,
            name,
            args.charge,
            args.mult,
            args.constraints_atoms,
            args.constraints_dist,
            args.constraints_angle,
            args.constraints_dihedral,
        )
        job_inputs.append(obj)
    return job_inputs


def generate_mol_from_cdx(csearch_file):
    cmd_cdx = ["obabel", "-icdx", csearch_file, "-osmi", "-Ocdx.smi"]
    subprocess.run(cmd_cdx, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open("cdx.smi", "r") as smifile:
        smi_lines = [str(line.strip()) for line in smifile]
    os.remove("cdx.smi")
    molecules = []
    for smi in smi_lines:
        molecule = Chem.MolFromSmiles(smi)
        molecules.append((smi, molecule))
    return molecules


def prepare_com_files(args, csearch_file):
    job_inputs = []

    filename = os.path.basename(csearch_file)
    if filename.split('.')[1] in ["gjf", "com"]:
        xyz_file, _, _ = com_2_xyz(csearch_file)
        if args.charge is None:
            _, charge, _ = get_info_input(csearch_file)
        else:
            charge = args.charge
        if args.mult is None:
            _, _, mult = get_info_input(csearch_file)
        else:
            mult = args.mult
    else:
        xyz_file = csearch_file
        if args.charge is None:
            charge, _ = read_xyz_charge_mult(csearch_file)
        else:
            charge = args.charge
        if args.mult is None:
            _, mult = read_xyz_charge_mult(csearch_file)
        else:
            mult = args.mult
    _ = xyz_2_sdf(xyz_file)

    sdffile = f'{os.path.dirname(csearch_file)}/{filename.split(".")[0]}.sdf'

    suppl, _, _, _ = mol_from_sdf_or_mol_or_mol2(sdffile, "csearch", args)

    name = filename.split('.')[0]
    name = add_prefix_suffix(name, args)

    obj = (
        suppl[0],
        name,
        charge,
        mult,
        args.constraints_atoms,
        args.constraints_dist,
        args.constraints_angle,
        args.constraints_dihedral,
    )
    job_inputs.append(obj)
    if os.path.basename(csearch_file).split('.')[1] in ["gjf", "com"]:
        os.remove(xyz_file)
    os.remove(sdffile)

    return job_inputs


def prepare_pdb_files(args, csearch_file):
    filename = os.path.basename(csearch_file)
    sdffile = f'{os.path.dirname(csearch_file)}/{filename.split(".")[0]}.sdf'
    command_pdb = [
        "obabel",
        "-ipdb",
        csearch_file,
        "-osdf",
        f'-O{sdffile}',
    ]
    subprocess.run(command_pdb, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    job_inputs = prepare_sdf_files(args, csearch_file)
    os.remove(sdffile)
    return job_inputs


def prepare_sdf_files(args, csearch_file):
    filename = os.path.basename(csearch_file)
    sdffile = f'{os.path.dirname(csearch_file)}/{filename}'

    suppl, charges, mults, IDs = mol_from_sdf_or_mol_or_mol2(sdffile, "csearch", args)

    if sdffile.split('.')[0] in ['mol','mol2']:
        os.remove(f'{sdffile.split(".")[0]}.sdf')

    job_inputs = []
    for mol, charge, mult, name in zip(suppl, charges, mults, IDs):
        name = add_prefix_suffix(name, args)
        obj = (
            mol,
            name,
            charge,
            mult,
            args.constraints_atoms,
            args.constraints_dist,
            args.constraints_angle,
            args.constraints_dihedral,
        )
        job_inputs.append(obj)
    return job_inputs


def xyz_2_sdf(file):
    """
    Creates a .sdf file from a .xyz in the specified directory. If no directory
    is specified then the files are created in the current directory.

    Parameters
    ----------
    file : str
        Filename and extension of an existing .xyz file
    """

    name = str(file).split(".xyz")[0]
    command_xyz = ["obabel", "-ixyz", file, "-osdf", "-O" + name + ".sdf"]
    subprocess.run(command_xyz, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def check_constraints(self):
    if (
        (len(self.args.constraints_atoms) != 0)
        or (len(self.args.constraints_dist) != 0)
        or (len(self.args.constraints_angle) != 0)
        or (len(self.args.constraints_dihedral) != 0)
    ):
        complex_ts = True
    else:
        complex_ts = False

    return complex_ts


def com_2_xyz(input_file):
    """
    COM to XYZ to SDF for obabel
    """

    filename = os.path.basename(input_file).split('.')[0]
    path_xyz = f'{os.path.dirname(input_file)}/{filename}.xyz'

    # Create the 'xyz' file and/or get the total charge
    xyz, charge, mult = get_info_input(input_file)
    xyz_txt = "\n".join(xyz)
    with open(path_xyz, "w") as F:
        F.write(f"{len(xyz)}\n{filename}\n{xyz_txt}\n")

    return path_xyz, charge, mult


def minimize_rdkit_energy(mol, conf, log, FF, maxsteps):
    """
    Minimizes a conformer of a molecule and returns the final energy.
    """

    if FF == "MMFF":
        properties = Chem.MMFFGetMoleculeProperties(mol)
        forcefield = Chem.MMFFGetMoleculeForceField(mol, properties, confId=conf)

    if FF == "UFF" or forcefield is None:
        # if forcefield is None means that MMFF will not work. Attempt UFF.
        forcefield = Chem.UFFGetMoleculeForceField(mol, confId=conf)

    if FF not in ["MMFF", "UFF"] or forcefield is None:
        log.write(f" Force field {FF} not supported!")
        log.finalize()
        sys.exit()

    forcefield.Initialize()
    try:
        forcefield.Minimize(maxIts=maxsteps)
    except RuntimeError:
        log.write(f"\nx  Geometry minimization failed with {FF}, using non-optimized geometry.")
    energy = float(forcefield.CalcEnergy())

    return energy


def getDihedralMatches(mol, heavy):
    # this is rdkit's "strict" pattern
    pattern = r"*~[!$(*#*)&!D1&!$(C(F)(F)F)&!$(C(Cl)(Cl)Cl)&!$(C(Br)(Br)Br)&!$(C([CH3])([CH3])[CH3])&!$([CD3](=[N,O,S])-!@[#7,O,S!D1])&!$([#7,O,S!D1]-!@[CD3]=[N,O,S])&!$([CD3](=[N+])-!@[#7!D1])&!$([#7!D1]-!@[CD3]=[N+])]-!@[!$(*#*)&!D1&!$(C(F)(F)F)&!$(C(Cl)(Cl)Cl)&!$(C(Br)(Br)Br)&!$(C([CH3])([CH3])[CH3])]~*"
    qmol = Chem.MolFromSmarts(pattern)
    matches = mol.GetSubstructMatches(qmol)

    # these are all sets of 4 atoms, uniquify by middle two
    uniqmatches = []
    seen = set()
    for (a, b, c, d) in matches:
        if (b, c) not in seen and (c, b) not in seen:
            if heavy:
                if (
                    mol.GetAtomWithIdx(a).GetSymbol() != "H"
                    and mol.GetAtomWithIdx(d).GetSymbol() != "H"
                ):
                    seen.add((b, c))
                    uniqmatches.append((a, b, c, d))
            if not heavy:
                if (
                    mol.GetAtomWithIdx(c).GetSymbol() == "C"
                    and mol.GetAtomWithIdx(d).GetSymbol() == "H"
                ):
                    pass
                else:
                    seen.add((b, c))
                    uniqmatches.append((a, b, c, d))
    return uniqmatches


def smi_to_mol(
    smi,
    program,
    log,
    seed,
    constraints_atoms,
    constraints_dist,
    constraints_angle,
    constraints_dihedral,
):

    complex_ts = False
    smi = smi.split(".")
    if (
        len(smi) > 1
        or len(constraints_atoms) != 0
        or len(constraints_dist) != 0
        or len(constraints_angle) != 0
        or len(constraints_dihedral) != 0
    ):
        if program not in ["crest"]:
            log.write("\nx  Program not supported for conformer generation of complexes and TSs! Specify: program='crest' for complexes")
            sys.exit()

        (
            mol,
            constraints_atoms,
            constraints_dist,
            constraints_angle,
            constraints_dihedral,
        ) = nci_ts_mol(
            smi,
            log,
            seed,
            constraints_atoms,
            constraints_dist,
            constraints_angle,
            constraints_dihedral,
        )
        complex_ts = True

    else:
        params = Chem.SmilesParserParams()
        params.removeHs = False
        try:
            mol = Chem.MolFromSmiles(smi[0], params)
        except Chem.AtomValenceException:
            log.write(f"\nx  The SMILES string provided ( {smi[0]} ) contains errors or the molecule needs to be drawn in a different way. For example, N atoms from ligands of metal complexes should be N+ since they're drawn with four bonds in ChemDraw, same for O atoms in carbonyl ligands, etc.\n")
            mol = None

    return (
        mol,
        constraints_atoms,
        constraints_dist,
        constraints_angle,
        constraints_dihedral,
        complex_ts
    )

def cluster_conformers(mols, heavy_only, max_matches_rmsd, cluster_thr):
    dists = []
    for i in range(len(mols)):
        for j in range(i):
            dists.append(get_conf_RMS(mols[i],mols[j], i, j, heavy_only, max_matches_rmsd))

    clusts = Butina.ClusterData(dists, len(mols), cluster_thr, isDistData=True, reordering=True)
    centroids = [x[0] for x in clusts]
    cluster_mols = [mols[x] for x in centroids]

    return cluster_mols, centroids

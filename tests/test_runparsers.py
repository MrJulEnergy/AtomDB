from __future__ import annotations

from atomdb.runparsers.plumed import parse_plumed

PLUMED = """
# KCl ion pair dissociation
UNITS LENGTH=A
d1: DISTANCE ATOMS=1,2
cn: COORDINATION GROUPA=1 GROUPB=3-50 R_0=3.5
METAD ...
  LABEL=metad
  ARG=d1,cn SIGMA=0.1,0.2 HEIGHT=1.2 PACE=500
  BIASFACTOR=10 TEMP=400 GRID_MIN=0,0 GRID_MAX=12,10
...
uwall: UPPER_WALLS ARG=d1 AT=10.0 KAPPA=150.0
PRINT ARG=d1,cn,metad.bias STRIDE=100 FILE=COLVAR
"""


def test_plumed_metad():
    r = parse_plumed(PLUMED)
    assert r["method"] == "metadynamics"
    assert [c["label"] for c in r["collective_variables"]] == ["d1", "cn"]
    assert r["collective_variables"][0]["ATOMS"] == "1,2"
    m = r["bias"]["metad"]
    assert m["PACE"] == 500 and m["BIASFACTOR"] == 10 and m["TEMP"] == 400
    assert m["SIGMA"] == [0.1, 0.2]
    assert r["bias"]["uwall"]["KAPPA"] == 150.0


def test_plumed_umbrella_and_opes():
    assert parse_plumed("d: DISTANCE ATOMS=1,2\nr: RESTRAINT ARG=d AT=3.0 KAPPA=500")[
        "method"] == "umbrella sampling"
    assert parse_plumed("d: DISTANCE ATOMS=1,2\nOPES_METAD ARG=d PACE=500 BARRIER=40")[
        "method"] == "OPES"
    assert parse_plumed("d: DISTANCE ATOMS=1,2\n")["method"] is None

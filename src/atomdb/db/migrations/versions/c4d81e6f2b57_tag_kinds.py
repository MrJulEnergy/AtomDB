"""tag registry: kind, description, alias_of (+ seed of the existing tags)

Revision ID: c4d81e6f2b57
Revises: b7e2f4a91c3d
Create Date: 2026-10-10 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4d81e6f2b57'
down_revision = 'b7e2f4a91c3d'
branch_labels = None
depends_on = None

#: the former hard-coded chip lists (services/labels.py) plus obvious campaign tags;
#: everything else starts as "free" and is edited on the Tags page / `atomdb tags set`
SEED = {
    "system": ["water", "kcl", "nacl", "peptide", "protein", "materials", "pore", "monomer",
               "dimer", "solvated", "ice", "salt", "electrolyte", "interface", "kcl-solution"],
    "project": ["kcl_explore", "active-learning", "fps", "training-set", "r2scan-candidates",
                "distillation", "alchemy", "peptide_generator", "patmad_sampling",
                "cluster_vs_pbc", "subset", "subset1", "subset2", "subset3", "jitter",
                "collection"],
    "quality": ["unreliable", "stale-stress", "force-outliers", "mislabelled-name",
                "duplicate-labels", "partial", "broken"],
}
ALIASES = {"Water": "water", "peptides": "peptide", "KCl_Solution": "kcl-solution"}


def upgrade() -> None:
    conn = op.get_bind()
    # leftover of an earlier batch-mode attempt (a table rebuild is refused by SQLite while
    # node_tag references tag); plain ADD COLUMN works in place
    conn.execute(sa.text("DROP TABLE IF EXISTS _alembic_tmp_tag"))
    op.add_column('tag', sa.Column('kind', sa.String(length=16), nullable=False,
                                   server_default='free'))
    op.add_column('tag', sa.Column('description', sa.Text(), nullable=True))
    # SQLite accepts REFERENCES on ADD COLUMN; alembic would emit a separate ALTER
    conn.execute(sa.text("ALTER TABLE tag ADD COLUMN alias_of INTEGER REFERENCES tag (id)"))

    ids = {name: i for i, name in conn.execute(sa.text("SELECT id, name FROM tag"))}
    lower = {n.lower(): n for n in ids}
    for kind, names in SEED.items():
        for n in names:
            if n in lower:
                conn.execute(sa.text("UPDATE tag SET kind = :k WHERE id = :i"),
                             {"k": kind, "i": ids[lower[n]]})
    for alias, canon in ALIASES.items():
        if alias not in ids or canon not in ids or alias == canon:
            continue
        a, c = ids[alias], ids[canon]
        conn.execute(sa.text(
            "INSERT OR IGNORE INTO node_tag (node_id, tag_id) "
            "SELECT node_id, :c FROM node_tag WHERE tag_id = :a"), {"a": a, "c": c})
        conn.execute(sa.text("DELETE FROM node_tag WHERE tag_id = :a"), {"a": a})
        conn.execute(sa.text("UPDATE tag SET alias_of = :c, kind = "
                             "(SELECT kind FROM tag WHERE id = :c) WHERE id = :a"), {"a": a, "c": c})


def downgrade() -> None:
    for col in ('alias_of', 'description', 'kind'):
        op.drop_column('tag', col)

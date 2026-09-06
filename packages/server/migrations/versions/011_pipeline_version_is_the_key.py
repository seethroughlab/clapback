"""The pipeline identity becomes the corpus key.

`ADR-0006` phase 4 of point 6: "Server switches the key, drops the legacy rows, and
begins rejecting undeclared contributions per point 4."

The key was `(fingerprint_hash, analysis_version, clap_model_version)`, and neither
version component meant what a key needs to mean. `clap_model_version` is the
checkpoint, which windowing or pooling moves every vector without touching;
`analysis_version` is a client's own counter, whose history includes a bump taken to
stay in step with an unrelated feature version and another (Familiar's v8) taken to
force a recompute while the vectors stayed identical. `pipeline_version` is composed
from every component that can move a vector, so two rows share a key exactly when
they are comparable — which is the property the key existed to have and never had.

**This migration deletes data, and it is the only one here that does.** Every row
that cannot say what produced it goes: `ADR-0006` point 5 decided those are
recomputed rather than relabelled, and a null cannot sit in a primary key in any
case. On the corpus as it stands that is all 47,486 rows, which is why the guard
below exists.

Revision ID: 011_pipeline_version_is_the_key
Revises: 010_embeddings_pipeline_version
Create Date: 2026-09-06
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011_pipeline_version_is_the_key"
down_revision: str | None = "010_embeddings_pipeline_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Set to `1` to run this when it would leave the corpus empty. There is one
#: legitimate reason to: a fresh database, where deleting nothing is the same as
#: deleting everything. Every other time it means the phases were run out of order.
_OVERRIDE = "CLAPBACK_ALLOW_EMPTYING_THE_CORPUS"


def upgrade() -> None:
    bind = op.get_bind()

    total = bind.execute(sa.text("SELECT count(*) FROM embeddings")).scalar_one()
    declared = bind.execute(
        sa.text("SELECT count(*) FROM embeddings WHERE pipeline_version IS NOT NULL")
    ).scalar_one()

    # **The guard, and the reason it is worth the lines.** Phases 2 and 3 put the
    # declaration on the client and recompute the library; this phase assumes they
    # have run and that the corpus has been repopulated with rows that say what
    # produced them. Run out of order it does exactly what it is written to do —
    # remove every row that cannot say — and on a corpus nobody has repopulated,
    # that is the whole commons. It is contributed data, and no backup restores the
    # afternoon somebody spends discovering the order was wrong.
    if total and not declared and os.environ.get(_OVERRIDE) != "1":
        raise RuntimeError(
            f"Refusing to run: {total:,} rows, none of which declare a pipeline. "
            f"This migration removes every row that cannot say what produced it, so "
            f"here it would empty the corpus. That means ADR-0006 phases 2 and 3 have "
            f"not reached this deployment yet — the client must be sending "
            f"pipeline_version, and its re-analysis must have repopulated the corpus, "
            f"before the key can change. If you are certain (a fresh database, say), "
            f"set {_OVERRIDE}=1."
        )

    # Point 5: recomputed, not relabelled. Anything still null here is a vector
    # whose provenance nobody recorded, and phase 4 is where that stops being
    # something the corpus carries.
    op.execute("DELETE FROM embeddings WHERE pipeline_version IS NULL")

    # **Collapse rows that the old key kept apart and the new one cannot.** Two rows
    # can share a hash and a pipeline while differing in `analysis_version` or
    # `clap_model_version` — a client that bumped its own counter without changing
    # the pipeline produces exactly that, and Familiar's v7 to v8 bump is that case
    # by construction.
    #
    # The survivor is the earliest, matching the first-write-wins rule the
    # contribution path has always used. Their `contributor_count`s are summed
    # rather than discarded: each was a real submission of this recording under this
    # pipeline, and `ADR-0004` point 4 already warns that the number counts
    # submissions rather than independent contributors, so summing loses nothing the
    # field was claiming.
    op.execute(
        """
        WITH ranked AS (
            SELECT ctid,
                   row_number() OVER (
                       PARTITION BY fingerprint_hash, pipeline_version
                       ORDER BY created_at, ctid
                   ) AS rn,
                   sum(contributor_count) OVER (
                       PARTITION BY fingerprint_hash, pipeline_version
                   ) AS total_count
            FROM embeddings
        )
        UPDATE embeddings e
           SET contributor_count = r.total_count
          FROM ranked r
         WHERE e.ctid = r.ctid AND r.rn = 1 AND r.total_count <> e.contributor_count
        """
    )
    op.execute(
        """
        DELETE FROM embeddings e
         USING (
            SELECT ctid,
                   row_number() OVER (
                       PARTITION BY fingerprint_hash, pipeline_version
                       ORDER BY created_at, ctid
                   ) AS rn
              FROM embeddings
         ) r
         WHERE e.ctid = r.ctid AND r.rn > 1
        """
    )

    op.alter_column("embeddings", "pipeline_version", nullable=False)

    # Points 2 and 3: both leave the key and stay as recorded columns. The checkpoint
    # is already the first component of the identity string, so keying on it
    # separately let two fields disagree about one fact; the client's counter is
    # useful provenance and was never a statement about comparability.
    op.drop_constraint("embeddings_pkey", "embeddings", type_="primary")
    op.create_primary_key(
        "embeddings_pkey", "embeddings", ["fingerprint_hash", "pipeline_version"]
    )

    # `analysis_version` and `clap_model_version` are now filters on the read path
    # rather than key components, and the lookup still accepts both.
    op.create_index(
        "ix_embeddings_analysis_version", "embeddings", ["analysis_version"]
    )

    # `submission_agreement` records against the same identity. Point 7 already
    # guarantees a row here compares two vectors claiming one pipeline, so that is
    # what the index should be able to find them by.
    op.create_index(
        "ix_submission_agreement_pipeline",
        "submission_agreement",
        ["fingerprint_hash", "pipeline_version"],
    )


def downgrade() -> None:
    """Restores the old key. It cannot restore the removed rows.

    Recorded plainly rather than raising: a downgrade is how someone gets a broken
    deployment serving again, and refusing to run would not give them their rows
    back either. Restore from the nightly `pg_dump` if the data is what is wanted.
    """
    op.drop_index("ix_submission_agreement_pipeline", table_name="submission_agreement")
    op.drop_index("ix_embeddings_analysis_version", table_name="embeddings")
    op.drop_constraint("embeddings_pkey", "embeddings", type_="primary")
    op.create_primary_key(
        "embeddings_pkey",
        "embeddings",
        ["fingerprint_hash", "analysis_version", "clap_model_version"],
    )
    op.alter_column("embeddings", "pipeline_version", nullable=True)

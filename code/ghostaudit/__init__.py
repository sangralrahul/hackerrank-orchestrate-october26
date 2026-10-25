"""Ghost Data Still Exists - deletion audit agent.

The package is organised as a pipeline:

    loaders      -> read the participant-facing CSV files
    identity     -> resolve a request e-mail to a subject and all of its identifiers
    evidence/*   -> turn every evidence file (JSON, SQL, logs, text, PDF, PNG)
                    into normalised ``Observation`` records
    activity     -> index system_activity.csv (claims, deletion-worker batches, catalog links)
    intent       -> decide whether a request authorises deletion (rules + optional LLM)
    linker       -> connect observations / resources to the subject of a case
    policy       -> apply deletion_rules.csv to the reconstructed state of each record
    planner      -> emit ordered FINDING / REMEDIATION / VERIFICATION steps
    engine       -> orchestrates one case or the whole request file
    llm          -> optional model client with caching and usage accounting

Everything is deterministic unless an API key is present and the LLM is enabled.
"""

__version__ = "1.0.0"

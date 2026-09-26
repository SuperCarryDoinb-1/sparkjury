# Skill card: sparkjury-clean

| Field | Value |
|---|---|
| Owner | SparkJury team (DGX Spark Hackathon, 3rd edition) |
| Version | 0.1.0 |
| Product | SparkJury agent evaluation harness |
| Underlying command | `sparkjury ingest + precheck` |
| Risk level | write (local store) |
| Data handling | Reads local trace files / SQLite stores only. Sends transcript excerpts to configured judge endpoints (local vLLM by default) and, when `TYPESAFE_API_KEY` is set, disagreement summaries to the Jev cloud API. Nothing else leaves the machine. |
| Network | Optional: StepFun / Jev / vLLM endpoints as configured |
| Side effects | Writes to the given SQLite store and `runs/<run_id>/` |
| Evaluation dataset | `../../data/samples/` (tau2 + OTel samples), `../../tests/` |
| Signature | `skill.oms.sig` to be produced with `model_signing` before publishing (see `../../scripts/sign_skills.sh`) |

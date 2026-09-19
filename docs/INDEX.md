# Documentation Index

## Quick Start
- **[../README.md#quick-start](../README.md#quick-start)** - The canonical quick start (see also the deployment decision tree in [../DEPLOYMENT.md](../DEPLOYMENT.md))
- **[DEMO_RUNBOOK.md](DEMO_RUNBOOK.md)** - Stand up the full stack for a demo (default docker-compose.yml)

## Core Documentation
- **[../README.md](../README.md)** - Project overview: analytics, methodology, deployment
- **[../CLAUDE.md](../CLAUDE.md)** - Complete architecture and development guide

## Analytics & Methodology
- **[../docs/reports/README.md](reports/README.md)** - The analytic insight reports: MENA theater assessment, initiator/category/recipient reports (served in-app at `/intel-reports`)
- **[INSIGHT_REPORT_PROMPT.md](INSIGHT_REPORT_PROMPT.md)** - Analytic doctrine: provenance normalization, the corroborated-initiative gate, report-generation playbook
- **[reports/_derived/manifest.md](reports/_derived/manifest.md)** - Derived analytics artifacts (DDL + promotion recommendations)
- **[Soft_Power_Analytics_White_Paper.md](Soft_Power_Analytics_White_Paper.md)** - Platform white paper (v6.1)

## Deployment Guides
- **[../DEPLOYMENT.md](../DEPLOYMENT.md)** - Start here: decision tree routing to the right deployment doc
- **[../PRODUCTION_DOCKER_RUN.md](../PRODUCTION_DOCKER_RUN.md)** - Enterprise / hardened-daemon deployment (raw docker run)
- **[ENTERPRISE_AGENT_RUNBOOK.md](ENTERPRISE_AGENT_RUNBOOK.md)** - Enterprise deploy sequence: host proxy + container versions, LLM env traps, verification checks
- **[ENTERPRISE_DELTA_RUNBOOK.md](ENTERPRISE_DELTA_RUNBOOK.md)** - Text-only incremental data transfer (gzipped CSV bundle) when pg_dump files cannot be moved
- **[SURVEY_ENTERPRISE_SETUP.md](SURVEY_ENTERPRISE_SETUP.md)** - Enabling the in-app feedback survey on the enterprise stack
- **[DOCKERHUB_README.md](DOCKERHUB_README.md)** - Docker Hub image documentation
- **[../DOCKER_WORKFLOW.md](../DOCKER_WORKFLOW.md)** - Docker build and workflow reference

## Pipeline & Services
- **[PIPELINE_REFRESH_RUNBOOK.md](PIPELINE_REFRESH_RUNBOOK.md)** - The canonical ordered refresh sequence for new document batches
- **[../services/PIPELINE_REFERENCE.md](../services/PIPELINE_REFERENCE.md)** - Complete pipeline workflow reference (documents, events, entities, summaries)
- **[../services/pipeline/batch/README_BATCH_PROCESSING.md](../services/pipeline/batch/README_BATCH_PROCESSING.md)** - OpenAI Batch API processing (prepare / queue / process)
- **[../services/pipeline/summaries/USAGE_GUIDE.md](../services/pipeline/summaries/USAGE_GUIDE.md)** - Event/period summary generation
- **[../services/pipeline/embeddings/README_BACKUP_RESTORE.md](../services/pipeline/embeddings/README_BACKUP_RESTORE.md)** - Embedding backup/restore (Parquet)
- **[../services/publication/README.md](../services/publication/README.md)** - Word-document publication service
- **[../agent/README.md](../agent/README.md)** - Conversational analyst assistant + report workflow
- **[../evals/README.md](../evals/README.md)** - Evaluation harness for every functional layer

## Security & Compliance
- **[deployment/ENTERPRISE_CVE_EXCEPTION_REQUEST.md](deployment/ENTERPRISE_CVE_EXCEPTION_REQUEST.md)** - Enterprise CVE exception template (regenerate scan evidence per submission)
- **[archive/CVE_MITIGATION_REPORT_1.5.5_2026-02.md](archive/CVE_MITIGATION_REPORT_1.5.5_2026-02.md)** - Archived CVE analysis snapshot (image 1.5.5 / pgvector 0.8.1-pg16 — not valid for later releases)

## Testing & CI/CD
- **[TESTING.md](TESTING.md)** - Testing guide, CI workflows, and honest coverage status

## Archived Documentation
Historical snapshots live in [`archive/`](archive/) — kept for reference only, not maintained:
- **[archive/MAINTAINABILITY_ASSESSMENT_2026-06.md](archive/MAINTAINABILITY_ASSESSMENT_2026-06.md)** - 2026-06 maintainability review & transition roadmap
- **[archive/ENTERPRISE_CUTOVER_RUNBOOK_1.8.18_2026.md](archive/ENTERPRISE_CUTOVER_RUNBOOK_1.8.18_2026.md)** - Point-in-time runbook for the 1.8.18 embedding-fix cutover + full rebuild
- **[archive/INGESTION_UI_DESIGN_2026-06.md](archive/INGESTION_UI_DESIGN_2026-06.md)** - Ingestion UI design proposal (Phase 1 shipped; see `services/pipeline/ingestion/README.md`)
- **[archive/CVE_MITIGATION_REPORT_1.5.5_2026-02.md](archive/CVE_MITIGATION_REPORT_1.5.5_2026-02.md)** - CVE scan/remediation snapshot for release 1.5.5

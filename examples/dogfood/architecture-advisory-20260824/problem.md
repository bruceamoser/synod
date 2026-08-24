Clarity AI Assistant needs a new document-ingestion worker. It takes incoming files (PDF, DOCX, email), runs them through extraction and chunking, and writes results to the vector store. Expected load for a single tenant: about 200 files/day, spiky (bursts of 50 files within a few minutes after a sync). The ops team is two engineers with no dedicated SRE and a shared on-call.

Question: should the ingestion worker be (a) a single long-lived service holding an in-process task queue, or (b) a set of short-lived per-batch jobs (one job per batch, triggered by schedule or webhook)?

Weigh failure isolation, cost, operational burden, and headroom. Recommend exactly one option and state the conditions under which you would change your mind.

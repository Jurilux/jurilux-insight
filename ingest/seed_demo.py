"""Insère quelques chunks de démonstration pour valider la chaîne complète
(Meilisearch -> /api/ask -> front) avant la reconstruction du vrai corpus.

Usage : python -m ingest.seed_demo
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.search import _client, ensure_index  # noqa: E402

DEMO = [
    {
        "chunk_id": "demo-0001",
        "doc_id": "csj_ch08_2019_demo1",
        "source_type": "jurisprudence",
        "juridiction_key": "csj_ch08",
        "year": 2019,
        "title": "CSJ 8e ch., arrêt de démonstration",
        "text": ("En matière de licenciement pour faute grave, l'article L.124-10 du Code du travail "
                 "exige que les faits reprochés rendent immédiatement et définitivement impossible "
                 "le maintien des relations de travail. La lettre de licenciement doit énoncer avec "
                 "précision les faits reprochés ; des motifs vagues équivalent à une absence de motifs."),
    },
    {
        "chunk_id": "demo-0002",
        "doc_id": "cassation_2021_demo2",
        "source_type": "jurisprudence",
        "juridiction_key": "cassation",
        "year": 2021,
        "title": "Cour de cassation, arrêt de démonstration",
        "text": ("La Cour rappelle que le délai d'un mois prévu à l'article L.124-10 (2) du Code du "
                 "travail pour notifier le licenciement avec effet immédiat court à partir du jour où "
                 "l'employeur a eu connaissance exacte des faits fautifs."),
    },
    {
        "chunk_id": "demo-0003",
        "doc_id": "eli-etat-leg-loi-2006-07-31-a149-consolide-20240901-fr-pdf.pdf",
        "source_type": "law",
        "year": 2006,
        "title": "Code du travail (extrait de démonstration)",
        "pdf_url": "https://legilux.public.lu/eli/etat/leg/code/travail",
        "text": ("Art. L.124-10. (1) Chacune des parties peut résilier le contrat de travail sans "
                 "préavis ou avant l'expiration du terme, pour un ou plusieurs motifs graves procédant "
                 "du fait ou de la faute de l'autre partie."),
    },
]


def main() -> None:
    ensure_index()
    idx = _client().index(settings.meili_index)
    task = idx.add_documents(DEMO)
    print(f"{len(DEMO)} chunks de démo envoyés (task {getattr(task, 'task_uid', task)}).")
    print('Test : curl -s -X POST localhost:8088/api/ask -H "Content-Type: application/json" '
          '-d \'{"q":"licenciement faute grave","topK":5,"temperature":0}\'')


if __name__ == "__main__":
    main()

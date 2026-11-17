"""Routes de la liste de comparaison (D-042) : indices et livrets proposés dans Performance."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app import db, repository
from app.schemas import BenchmarkIn

router = APIRouter()


@router.get("/api/benchmarks")
def list_benchmarks() -> list[dict]:
    with db.connect() as conn:
        return repository.list_benchmarks(conn)


@router.post("/api/benchmarks", status_code=201)
def create_benchmark(body: BenchmarkIn) -> dict:
    with db.connect() as conn:
        return repository.create_benchmark(
            conn, body.kind, body.label, body.grp, body.ticker, body.rate
        )


@router.patch("/api/benchmarks/{benchmark_id}")
def update_benchmark(benchmark_id: int, body: BenchmarkIn) -> dict:
    with db.connect() as conn:
        if repository.get_benchmark(conn, benchmark_id) is None:
            raise HTTPException(status_code=404, detail="Entrée inconnue.")
        return repository.update_benchmark(
            conn, benchmark_id, body.kind, body.label, body.grp, body.ticker, body.rate
        )


@router.post("/api/benchmarks/{benchmark_id}/move", status_code=204)
def move_benchmark(benchmark_id: int, step: Annotated[int, Query(ge=-1, le=1)]) -> None:
    """Monte (step=-1) ou descend (step=1) une entrée d'un cran."""
    with db.connect() as conn:
        if repository.get_benchmark(conn, benchmark_id) is None:
            raise HTTPException(status_code=404, detail="Entrée inconnue.")
        repository.move_benchmark(conn, benchmark_id, step)


@router.delete("/api/benchmarks/{benchmark_id}", status_code=204)
def delete_benchmark(benchmark_id: int) -> None:
    with db.connect() as conn:
        if not repository.delete_benchmark(conn, benchmark_id):
            raise HTTPException(status_code=404, detail="Entrée inconnue.")

"""Dataset adapters and the stable research dataset registry."""

from . import census13, forest10, power7

DATASETS = {
    census13.BENCHMARK_ID: census13,
    forest10.BENCHMARK_ID: forest10,
    power7.BENCHMARK_ID: power7,
}


def get_dataset(dataset_id: str):
    try:
        return DATASETS[dataset_id]
    except KeyError as exc:
        raise ValueError(f"unsupported dataset: {dataset_id}") from exc

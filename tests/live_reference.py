"""Row-wise reference: deliberately independent of Compiler.features/Engine."""
import numpy as np
import pandas as pd

from aheaddb.spec import timestamp


def reference_features(snapshot, task, cutoff):
    t = timestamp(cutoff)
    schema = snapshot.schema
    entities = snapshot.table(schema.entity_table)
    entities = sorted(entities.loc[entities[schema.available_at] <= t, schema.entity_key])
    records = []
    sources = {event.table: snapshot.table(event.table).to_dict("records") for event in schema.events}
    for entity in entities:
        values = {}
        for event in schema.events:
            source = sources[event.table]
            selected = sorted([row for row in source if row[event.entity_key] == entity
                               and t - pd.Timedelta(days=task.history_days) < row[event.occurred_at] <= t
                               and row[event.available_at] <= t],
                              key=lambda row: (row[event.occurred_at], row[event.available_at], row[event.key]))
            values[f"{event.table}__count"] = len(selected)
            values[f"{event.table}__recency"] = ((t - selected[-1][event.occurred_at]).total_seconds() / 86400
                                                if selected else task.history_days + 1)
            for column in event.values:
                numbers = [row[column] for row in selected if not pd.isna(row[column])]
                mean = float(np.mean(numbers)) if numbers else 0
                latest = numbers[-1] if numbers else 0
                prefix = f"{event.table}__{column}"
                values.update({prefix + "__sum": sum(numbers), prefix + "__mean": mean,
                               prefix + "__missing": len(selected) - len(numbers),
                               prefix + "__std": float(np.std(numbers)) if numbers else 0,
                               prefix + "__last": latest, prefix + "__trend": latest - mean})
            for days in (1, 7, 30):
                if days >= task.history_days:
                    continue
                recent = [row for row in selected if row[event.occurred_at] > t - pd.Timedelta(days=days)]
                prefix = f"{event.table}__recent{days}"
                values[prefix + "__count"] = len(recent)
                for column in event.values:
                    numbers = [row[column] for row in recent if not pd.isna(row[column])]
                    values[prefix + f"__{column}__sum"] = sum(numbers)
                    values[prefix + f"__{column}__mean"] = float(np.mean(numbers)) if numbers else 0
        records.append(values)
    return pd.DataFrame(records, index=pd.Index(entities, name="entity_id")).astype(float)

"""Favourite guachinches per user, in DynamoDB (FAVORITES_TABLE) or in memory for local runs."""

import os
from datetime import datetime, timezone

TABLE_NAME = os.environ.get("FAVORITES_TABLE")


class MemoryStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, dict]] = {}

    def put(self, user_id: str, item: dict) -> None:
        self._items.setdefault(user_id, {})[item["guachinche_id"]] = item

    def delete(self, user_id: str, guachinche_id: str) -> bool:
        return self._items.get(user_id, {}).pop(guachinche_id, None) is not None

    def list(self, user_id: str) -> list[dict]:
        return list(self._items.get(user_id, {}).values())


class DynamoStore:
    def __init__(self, table_name: str) -> None:
        import boto3

        self._table = boto3.resource("dynamodb").Table(table_name)

    def put(self, user_id: str, item: dict) -> None:
        self._table.put_item(Item={"user_id": user_id, **item})

    def delete(self, user_id: str, guachinche_id: str) -> bool:
        response = self._table.delete_item(
            Key={"user_id": user_id, "guachinche_id": guachinche_id}, ReturnValues="ALL_OLD"
        )
        return "Attributes" in response

    def list(self, user_id: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        items = self._table.query(KeyConditionExpression=Key("user_id").eq(user_id))["Items"]
        return [{k: v for k, v in item.items() if k != "user_id"} for item in items]


store = DynamoStore(TABLE_NAME) if TABLE_NAME else MemoryStore()


def new_item(guachinche_id: str, note: str | None) -> dict:
    item = {"guachinche_id": guachinche_id, "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if note:
        item["note"] = note
    return item

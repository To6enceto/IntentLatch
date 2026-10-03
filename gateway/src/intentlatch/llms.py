from dataclasses import dataclass

from .errors import GatewayError
from .settings import Settings

MODEL_A = "corporate-a"
MODEL_B = "corporate-b"
MODEL_IDS = (MODEL_A, MODEL_B)


@dataclass(frozen=True)
class CorporateLlms:
    tags: dict[str, str]

    @classmethod
    def from_settings(cls, settings: Settings) -> "CorporateLlms":
        return cls({MODEL_A: settings.model_a, MODEL_B: settings.model_b})

    @property
    def ids(self) -> list[str]:
        return list(self.tags)

    def tag_for(self, model_id: str) -> str:
        tag = self.tags.get(model_id)
        if tag is None:
            raise GatewayError(
                404, "model_not_found", f"Unknown model. Available models: {', '.join(self.tags)}."
            )
        return tag

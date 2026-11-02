from worker.app.config import Settings


def test_default_free_model_fallback_chain_contains_only_free_models_or_router() -> None:
    settings = Settings()
    models = [settings.openrouter_model, *settings.fallback_model_list]
    assert models[0] == "nvidia/nemotron-3.5-super-vl:free"
    assert all(model.endswith(":free") or model == "openrouter/free" for model in models)


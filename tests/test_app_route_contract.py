from main import _path_is_public, app


def test_core_routes_registered() -> None:
    route_paths = {route.path for route in app.routes}

    expected = {
        "/",
        "/health",
        "/ui/login",
        "/ui/setup-first-use",
        "/ui/client/new",
        "/upload/{token}",
    }
    missing = expected - route_paths
    assert not missing, f"Missing expected routes: {sorted(missing)}"


def test_public_path_rules() -> None:
    assert _path_is_public("/")
    assert _path_is_public("/health")
    assert _path_is_public("/ui/login")
    assert _path_is_public("/ui/setup-first-use")
    assert _path_is_public("/static/brand/icons/creditsapientia_black.ico")
    assert _path_is_public("/upload/token-123")
    assert not _path_is_public("/ui/dashboard")

from core.logging import configure_logging
from db.session import session_scope
from services.seed_demo import seed


def main() -> None:
    configure_logging()
    with session_scope() as session:
        seed(session)
    print("MOCK DATA loaded. Restarting the app will keep this database.")


if __name__ == "__main__":
    main()

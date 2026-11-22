import sqlite3
import sys

print("================================")
print(" BETCLIC BOOKMAKER INTELLIGENCE")
print(" TEST TECHNIQUE V8.1")
print("================================")

print(f"Python version : {sys.version}")

try:
    connection = sqlite3.connect(":memory:")
    connection.execute("""
        CREATE TABLE test (
            id INTEGER PRIMARY KEY,
            message TEXT
        )
    """)
    connection.execute(
        "INSERT INTO test (message) VALUES (?)",
        ("Systeme operationnel",)
    )

    result = connection.execute(
        "SELECT message FROM test"
    ).fetchone()

    print("SQLite : OK")
    print("Database :", result[0])

    connection.close()

except Exception as error:
    print("ERREUR :", error)

print("================================")
print(" TEST TERMINE")
print("================================")

import os

# Temporary development mode: the public app bypasses the login dependency.
# Set AUTH_BYPASS=0 in production to restore the normal authentication flow.
os.environ.setdefault("INITIAL_SETUP_CODE", "temporary-disabled-login")

import main_secure as secure

app = secure.app


def no_login():
    institute = secure.q(
        "SELECT id,institute_name,full_name,email FROM institutes ORDER BY id LIMIT 1",
        one=True,
    )

    if not institute:
        salt = secure.secrets.token_hex(16)
        with secure.db() as c:
            row = c.execute(
                "INSERT INTO institutes(institute_name,full_name,email,password_hash,password_salt) VALUES(%s,%s,%s,%s,%s) RETURNING id",
                (
                    "Algorithmic Demo Institute",
                    "Boss",
                    "demo@algorithmic.local",
                    secure.hash_pw("temporary-disabled-login", salt),
                    salt,
                ),
            ).fetchone()
            institute_id = row["id"]
            c.execute(
                "INSERT INTO branches(institute_id,name) VALUES(%s,'Main Campus')",
                (institute_id,),
            )
            c.commit()
        institute = secure.q(
            "SELECT id,institute_name,full_name,email FROM institutes WHERE id=%s",
            (institute_id,),
            one=True,
        )

    return {
        "id": institute["id"],
        "institute_name": institute["institute_name"],
        "full_name": institute["full_name"] or "",
        "email": institute["email"],
        "is_owner": True,
        "designation": "boss",
        "permissions": {m: "edit" for m in secure.VALID_MODULES},
    }


# FastAPI supports dependency overrides throughout the dependency graph,
# including dependencies that themselves depend on current().
app.dependency_overrides[secure.current] = no_login

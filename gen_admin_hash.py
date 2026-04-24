from werkzeug.security import generate_password_hash

h = generate_password_hash("Admin@Maxlien2025", method="scrypt")

with open("/tmp/admin_hash.txt", "w") as f:
    f.write(h)

print("HASH GERADO E SALVO EM /tmp/admin_hash.txt")

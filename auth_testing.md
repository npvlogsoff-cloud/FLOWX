# Auth Testing Playbook (FLOWX)

Step 1: MongoDB verification
```
mongosh
use test_database
db.users.find({role: "admin"}).pretty()
db.users.findOne({role: "admin"}, {password_hash: 1})
```
Verify bcrypt hash starts with `$2b$`; indexes exist on users.email (unique), login_attempts.identifier, password_reset_tokens.expires_at (TTL).

Step 2: API testing
```
curl -c cookies.txt -X POST http://localhost:8001/api/auth/login -H "Content-Type: application/json" -d '{"email":"admin@flowx.io","password":"Admin#2026"}'
cat cookies.txt
curl -b cookies.txt http://localhost:8001/api/auth/me
```
Login returns `{user, token}` and sets `access_token` + `refresh_token` cookies. `/me` works with cookies or `Authorization: Bearer <token>`.
Brute force: 5 failed logins per ip:email -> 429 for 15 minutes.

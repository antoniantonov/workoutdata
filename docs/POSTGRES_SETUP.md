# PostgreSQL Setup - Quick Start

## ✅ Created Files

### 1. Docker Configuration
- **`docker-compose.yml`** - PostgreSQL container configuration
  - Image: `postgres:17.2-alpine` (latest stable with specific tag)
  - Port: `5432` (standard PostgreSQL port)
  - Network: `workout_network` (bridge)
  - Data volume: `./data/pg/` → `/var/lib/postgresql/data`
  - Health check included

### 2. PostgreSQL Test Script
- **`tests/pg/pg_test.py`** - Comprehensive test script
- **`tests/pg/pyproject.toml`** - Dependencies for uv
- **`tests/pg/README.md`** - Detailed usage instructions
- **`tests/pg/.gitignore`** - Ignore virtual environments

### 3. Documentation
- **`DOCKER.md`** - Complete Docker operations guide
- **`data/pg/.gitkeep`** - Placeholder for data directory

### 4. Git Configuration
- Updated `.gitignore` to exclude PostgreSQL data files

## 🚀 Getting Started

### Step 1: Start PostgreSQL

```bash
# From repository root
docker-compose up -d
```

This will:
- Pull PostgreSQL 17.2 Alpine image
- Create `workout_network` network
- Start PostgreSQL on port 5432
- Create database `workout_data` with user `workout_user`
- Mount `./data/pg/` for data persistence

### Step 2: Wait for Health Check

```bash
# Check if PostgreSQL is ready
docker-compose ps

# Should show "healthy" status after ~10 seconds
# Or watch logs:
docker-compose logs -f postgres
```

### Step 3: Run Test Script

```bash
# Option 1: From tests/pg directory
cd tests/pg
uv run pg_test.py

# Option 2: From repository root
uv run tests/pg/pg_test.py
```

### Expected Output

```
============================================================
PostgreSQL Connection Test
============================================================

1. Connecting to PostgreSQL...
   ✅ Connected successfully!
   PostgreSQL version: PostgreSQL 17.2

2. Creating test table 'test_workouts'...
   ✅ Table created successfully!

3. Inserting test data...
   ✅ Inserted 3 test workouts

4. Reading data back...
   ✅ Retrieved 3 rows

   Workouts:
   --------------------------------------------------------
   Workout ID           Date                 Avg HR     Max HR    
   --------------------------------------------------------
   25-12-2025_103000    2025-12-25 10:30     145.5      178       
   26-12-2025_090000    2025-12-26 09:00     138.2      165       
   27-12-2025_164500    2025-12-27 16:45     152.8      185       

5. Testing aggregation query...
   ✅ Statistics calculated:
     - Total workouts: 3
     - Average HR: 145.5 bpm
     - Highest max HR: 185 bpm
     - Total calories: 1350

6. Cleaning up - dropping test table...
   ✅ Test table dropped successfully!

============================================================
✅ All tests passed successfully!
============================================================
```

## 📋 Database Connection Details

| Parameter | Value |
|-----------|-------|
| Host | `localhost` |
| Port | `5432` |
| Database | `workout_data` |
| User | `workout_user` |
| Password | `workout_password` |
| Data Volume | `./data/pg/` |

## 🔧 Common Operations

### Access PostgreSQL CLI

```bash
# Using Docker
docker-compose exec postgres psql -U workout_user -d workout_data

# Using local psql
psql -h localhost -U workout_user -d workout_data
```

### View Logs

```bash
docker-compose logs -f postgres
```

### Stop PostgreSQL

```bash
# Stop but keep data
docker-compose stop

# Stop and remove container (data persists)
docker-compose down

# Stop and remove everything including data
docker-compose down -v
rm -rf ./data/pg/*
```

### Check Health

```bash
docker-compose ps
# Look for "healthy" in Status column
```

## 📁 Directory Structure

```
workoutdata/
├── docker-compose.yml          # PostgreSQL container config
├── DOCKER.md                   # Detailed Docker guide
├── data/
│   └── pg/                     # PostgreSQL data volume
│       └── .gitkeep            # Track directory in git
└── tests/
    └── pg/
        ├── .gitignore          # Ignore venv
        ├── README.md           # Test documentation
        ├── pyproject.toml      # Dependencies
        └── pg_test.py          # Test script
```

## 🧪 What the Test Does

1. **Connects** to PostgreSQL database
2. **Creates** test table `test_workouts` with workout schema
3. **Inserts** 3 sample workout records
4. **Queries** data back and displays results
5. **Runs** aggregation queries (COUNT, AVG, MAX, SUM)
6. **Drops** test table (cleanup)

All operations use parameterized queries for security.

## 🐛 Troubleshooting

### Connection Refused
```
❌ Database error: could not connect to server
```
**Fix:** Start PostgreSQL with `docker-compose up -d`

### Port Already in Use
```
Error: Bind for 0.0.0.0:5432 failed
```
**Fix:** 
- Check if another PostgreSQL is running: `lsof -i :5432`
- Change port in `docker-compose.yml` and `pg_test.py`

### psycopg2 Not Installed
```
❌ psycopg2 not installed
```
**Fix:** `uv run` will automatically install it from `pyproject.toml`

## 📚 Next Steps

1. **Start PostgreSQL:** `docker-compose up -d`
2. **Run test:** `cd tests/pg && uv run pg_test.py`
3. **Explore CLI:** `docker-compose exec postgres psql -U workout_user -d workout_data`
4. **Read docs:** See `DOCKER.md` for comprehensive guide

## 🔐 Security Note

The credentials in `docker-compose.yml` are for **local development only**. For production:
- Use environment variables
- Never commit credentials to git
- Use Docker secrets or external secret management
- Restrict network access

---

**All set! Run `docker-compose up -d` to get started.** 🚀

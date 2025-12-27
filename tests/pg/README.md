# PostgreSQL Test

Test script for PostgreSQL database connection and basic operations.

## Prerequisites

1. **Start PostgreSQL with Docker Compose**

   From the repository root:
   ```bash
   docker-compose up -d
   ```

   This will start PostgreSQL on `localhost:5432` with:
   - Database: `workout_data`
   - User: `workout_user`
   - Password: `workout_password`
   - Data volume: `./data/pg/`

2. **Check PostgreSQL is running**

   ```bash
   docker-compose ps
   ```

   Wait until health status shows `healthy`.

## Running the Test

```bash
cd tests/pg
uv run pg_test.py
```

Or from the repository root:
```bash
uv run tests/pg/pg_test.py
```

## What the Test Does

1. ✅ Connects to PostgreSQL database
2. ✅ Creates a test table `test_workouts`
3. ✅ Inserts 3 sample workout records
4. ✅ Reads data back and displays it
5. ✅ Runs aggregation queries
6. ✅ Drops the test table (cleanup)

## Expected Output

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

## Troubleshooting

### Connection Refused
```
❌ Database error: could not connect to server: Connection refused
```

**Solution:** Start PostgreSQL with `docker-compose up -d`

### Authentication Failed
```
❌ Database error: FATAL:  password authentication failed
```

**Solution:** Check credentials in `docker-compose.yml` and `pg_test.py` match

### Port Already in Use
```
Error: Bind for 0.0.0.0:5432 failed: port is already allocated
```

**Solution:** 
- Stop existing PostgreSQL: `docker-compose down`
- Or change port in `docker-compose.yml` and `pg_test.py`

## Database Connection Details

- **Host:** localhost
- **Port:** 5432
- **Database:** workout_data
- **User:** workout_user
- **Password:** workout_password
- **Data Volume:** `../../data/pg/` (persisted on host)

## Stopping PostgreSQL

```bash
# Stop but keep data
docker-compose stop

# Stop and remove container (data persists in volume)
docker-compose down

# Stop and remove everything including data
docker-compose down -v
rm -rf ../../data/pg/*
```

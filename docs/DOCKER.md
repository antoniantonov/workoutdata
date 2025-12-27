# Docker Setup Guide

## PostgreSQL Database

### Quick Start

```bash
# Start PostgreSQL
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f postgres

# Stop PostgreSQL
docker-compose stop

# Stop and remove container (data persists)
docker-compose down
```

### Database Connection Details

- **Host:** localhost
- **Port:** 5432
- **Database:** workout_data
- **User:** workout_user
- **Password:** workout_password
- **Data Volume:** `./data/pg/` (persisted on host)

### Testing the Connection

```bash
# Run PostgreSQL test script
cd tests/pg
uv run pg_test.py
```

See [tests/pg/README.md](tests/pg/README.md) for details.

### Accessing PostgreSQL CLI

```bash
# Using Docker
docker-compose exec postgres psql -U workout_user -d workout_data

# Using local psql (if installed)
psql -h localhost -U workout_user -d workout_data
# Password: workout_password
```

### Useful PostgreSQL Commands

```sql
-- List all tables
\dt

-- Describe table structure
\d table_name

-- List all databases
\l

-- List all users
\du

-- Quit
\q
```

### Data Persistence

- PostgreSQL data is stored in `./data/pg/` directory
- This directory is mounted as a Docker volume
- Data persists even after `docker-compose down`
- To completely remove data: `rm -rf ./data/pg/*`

### Network

- All services run on the `workout_network` bridge network
- This allows inter-container communication if you add more services

### Health Check

PostgreSQL container includes a health check:
- Command: `pg_isready -U workout_user -d workout_data`
- Interval: 10 seconds
- Timeout: 5 seconds
- Retries: 5

Check health status:
```bash
docker-compose ps
# Look for "healthy" in the Status column
```

### Troubleshooting

#### Port Already in Use
```bash
# Find what's using port 5432
lsof -i :5432

# Change port in docker-compose.yml (e.g., "5433:5432")
```

#### Permission Issues
```bash
# Fix data directory permissions
sudo chown -R $(id -u):$(id -g) ./data/pg/
```

#### Container Won't Start
```bash
# View detailed logs
docker-compose logs postgres

# Remove and recreate
docker-compose down
rm -rf ./data/pg/*
docker-compose up -d
```

#### Reset Database
```bash
# Stop container and remove data
docker-compose down
rm -rf ./data/pg/*

# Start fresh
docker-compose up -d
```

## Future Extensions

You can add more services to `docker-compose.yml`:

```yaml
services:
  postgres:
    # ... existing config ...
  
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    networks:
      - workout_network
  
  pgadmin:
    image: dpage/pgadmin4:latest
    environment:
      PGADMIN_DEFAULT_EMAIL: admin@workout.local
      PGADMIN_DEFAULT_PASSWORD: admin
    ports:
      - "5050:80"
    networks:
      - workout_network
```

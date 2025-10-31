#!/bin/bash

# Shell script to copy DuckDB file and launch interactive session
# Usage: ./run_duckdb_local.sh
echo "Starting DuckDB local session..."

# Configuration
SOURCE_DB="../hr_data/database_v2.duckdb"
WORK_DIR="../temp"
DB_NAME="database_v2.duckdb"
WORK_DB="${WORK_DIR}/${DB_NAME}"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}🗄️  DuckDB Launcher Script${NC}"
echo "================================="

# Check if source database exists
if [ ! -f "$SOURCE_DB" ]; then
    echo -e "${RED}❌ Error: Source database not found at $SOURCE_DB${NC}"
    exit 1
fi

# Create working directory if it doesn't exist
if [ ! -d "$WORK_DIR" ]; then
    echo -e "${YELLOW}📁 Creating working directory: $WORK_DIR${NC}"
    mkdir -p "$WORK_DIR"
fi

# Copy database to working folder
echo -e "${YELLOW}📋 Copying database to working folder...${NC}"
cp "$SOURCE_DB" "$WORK_DB"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Database copied successfully${NC}"
    echo -e "${GREEN}📍 Working database location: $WORK_DB${NC}"
else
    echo -e "${RED}❌ Error: Failed to copy database${NC}"
    exit 1
fi

# Display database info
echo ""
echo "Database Information:"
echo "--------------------"
echo "Source:  $SOURCE_DB"
echo "Working: $WORK_DB"
echo "Size:    $(du -h "$WORK_DB" | cut -f1)"
echo ""

# Launch DuckDB interactive session
echo -e "${GREEN}🚀 Launching DuckDB interactive session...${NC}"
echo -e "${YELLOW}💡 Tip: Use .tables to see available tables${NC}"
echo -e "${YELLOW}💡 Tip: Use .exit or Ctrl+D to quit${NC}"
echo ""

# Change to working directory and launch DuckDB
pushd "$WORK_DIR"
duckdb "$DB_NAME" -ui
popd

echo ""
echo -e "${GREEN}👋 DuckDB session ended${NC}"
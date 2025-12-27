"""PostgreSQL connection and basic operations test script.

This script tests the PostgreSQL database connection by:
1. Connecting to the database
2. Creating a test table
3. Inserting test data
4. Reading data back
5. Dropping the test table
"""
import sys
from datetime import datetime
from typing import List, Tuple

try:
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
except ImportError:
    print("❌ psycopg2 not installed. Install with: pip install psycopg2-binary")
    sys.exit(1)


# Database connection parameters
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'database': 'workout_data',
    'user': 'workout_user',
    'password': 'workout_password'
}


def test_postgres_connection():
    """Test PostgreSQL connection and basic operations."""
    
    print("=" * 60)
    print("PostgreSQL Connection Test")
    print("=" * 60)
    
    connection = None
    cursor = None
    
    try:
        # Step 1: Connect to PostgreSQL
        print("\n1. Connecting to PostgreSQL...")
        connection = psycopg2.connect(**DB_CONFIG)
        connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = connection.cursor()
        
        # Get PostgreSQL version
        cursor.execute("SELECT version();")
        version = cursor.fetchone()[0]
        print(f"   ✅ Connected successfully!")
        print(f"   PostgreSQL version: {version.split(',')[0]}")
        
        # Step 2: Create test table
        print("\n2. Creating test table 'test_workouts'...")
        cursor.execute("""
            DROP TABLE IF EXISTS test_workouts CASCADE;
        """)
        
        cursor.execute("""
            CREATE TABLE test_workouts (
                id SERIAL PRIMARY KEY,
                workout_id VARCHAR(50) UNIQUE NOT NULL,
                workout_date TIMESTAMP NOT NULL,
                duration_seconds INTEGER NOT NULL,
                avg_heart_rate DECIMAL(5, 2),
                max_heart_rate INTEGER,
                calories INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("   ✅ Table created successfully!")
        
        # Step 3: Insert test data
        print("\n3. Inserting test data...")
        test_workouts = [
            ('25-12-2025_103000', datetime(2025, 12, 25, 10, 30, 0), 3600, 145.5, 178, 450),
            ('26-12-2025_090000', datetime(2025, 12, 26, 9, 0, 0), 2700, 138.2, 165, 380),
            ('27-12-2025_164500', datetime(2025, 12, 27, 16, 45, 0), 4200, 152.8, 185, 520),
        ]
        
        for workout in test_workouts:
            cursor.execute("""
                INSERT INTO test_workouts 
                (workout_id, workout_date, duration_seconds, avg_heart_rate, max_heart_rate, calories)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, workout)
        
        print(f"   ✅ Inserted {len(test_workouts)} test workouts")
        
        # Step 4: Read data back
        print("\n4. Reading data back...")
        cursor.execute("""
            SELECT 
                workout_id,
                workout_date,
                duration_seconds,
                avg_heart_rate,
                max_heart_rate,
                calories
            FROM test_workouts
            ORDER BY workout_date;
        """)
        
        results = cursor.fetchall()
        print(f"   ✅ Retrieved {len(results)} rows")
        print("\n   Workouts:")
        print("   " + "-" * 56)
        print(f"   {'Workout ID':<20} {'Date':<20} {'Avg HR':<10} {'Max HR':<10}")
        print("   " + "-" * 56)
        
        for row in results:
            workout_id, workout_date, duration, avg_hr, max_hr, calories = row
            print(f"   {workout_id:<20} {workout_date.strftime('%Y-%m-%d %H:%M'):<20} {avg_hr:<10.1f} {max_hr:<10}")
        
        # Step 5: Test aggregation query
        print("\n5. Testing aggregation query...")
        cursor.execute("""
            SELECT 
                COUNT(*) as total_workouts,
                AVG(avg_heart_rate) as overall_avg_hr,
                MAX(max_heart_rate) as highest_max_hr,
                SUM(calories) as total_calories
            FROM test_workouts;
        """)
        
        stats = cursor.fetchone()
        print(f"   ✅ Statistics calculated:")
        print(f"     - Total workouts: {stats[0]}")
        print(f"     - Average HR: {stats[1]:.1f} bpm")
        print(f"     - Highest max HR: {stats[2]} bpm")
        print(f"     - Total calories: {stats[3]}")
        
        # Step 6: Drop test table
        print("\n6. Cleaning up - dropping test table...")
        cursor.execute("DROP TABLE IF EXISTS test_workouts CASCADE;")
        print("   ✅ Test table dropped successfully!")
        
        print("\n" + "=" * 60)
        print("✅ All tests passed successfully!")
        print("=" * 60)
        
        return True
        
    except psycopg2.Error as e:
        print(f"\n❌ Database error: {e}")
        print(f"   Error code: {e.pgcode}")
        print(f"   Error details: {e.pgerror}")
        return False
        
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return False
        
    finally:
        # Close cursor and connection
        if cursor:
            cursor.close()
        if connection:
            connection.close()
            print("\nConnection closed.")


if __name__ == "__main__":
    success = test_postgres_connection()
    sys.exit(0 if success else 1)

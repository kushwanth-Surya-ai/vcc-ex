import os
import sys
import asyncpg
import asyncio
from dotenv import load_dotenv
from urllib.parse import urlparse

async def create_database():
    # Load environment variables
    load_dotenv()
    
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL not found in .env")
        sys.exit(1)
        
    # Parse the URL
    # e.g. postgresql+asyncpg://postgres:password@localhost:5432/vcc_db
    # We need to strip +asyncpg for urlparse to work correctly sometimes, but we can do it manually
    url_no_driver = db_url.replace("+asyncpg", "")
    parsed = urlparse(url_no_driver)
    
    db_name = parsed.path.lstrip('/')
    user = parsed.username
    password = parsed.password
    host = parsed.hostname
    port = parsed.port or 5432
    
    print(f"Attempting to create database '{db_name}' at {host}:{port}...")
    
    try:
        # Connect to the default 'postgres' database to create the new one
        conn = await asyncpg.connect(
            user=user,
            password=password,
            host=host,
            port=port,
            database='postgres'
        )
        
        # Check if database exists
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )
        
        if not exists:
            # Create database
            print(f"Database '{db_name}' does not exist. Creating it now...")
            await conn.execute(f'CREATE DATABASE "{db_name}"')
            print(f"Database '{db_name}' created successfully!")
        else:
            print(f"Database '{db_name}' already exists.")
            
        await conn.close()
    except asyncpg.exceptions.InvalidPasswordError:
        print("\n" + "="*60)
        print("DATABASE AUTHENTICATION ERROR")
        print("="*60)
        print(f"Failed to connect to PostgreSQL as user '{user}'.")
        print("The password in backend/.env does not match your PostgreSQL password.")
        print("Please edit backend/.env, update the DATABASE_URL with your correct PostgreSQL password, and try again.")
        print("="*60 + "\n")
        sys.exit(1)
    except Exception as e:
        print(f"\nError connecting to PostgreSQL: {e}")
        print("Ensure PostgreSQL is running and the credentials in backend/.env are correct.\n")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(create_database())

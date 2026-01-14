"""Main entry point for the application."""
import uvicorn

if __name__ == "__main__":
    print("\n🏛️  Federal Regulations Analyzer")
    print("=" * 50)
    print("Starting server...")
    print("Open: http://localhost:8000")
    print("=" * 50 + "\n")

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)

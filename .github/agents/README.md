# Custom Copilot Agents

This directory contains instruction files for specialized GitHub Copilot agents that provide domain-specific expertise for the workoutdata repository.

## Available Agents

### 1. Python Backend Agent (`python-backend.md`)
**Expertise**: Backend API development, data processing, and database operations

**Use when**:
- Creating or modifying backend API endpoints
- Implementing data processing pipelines
- Working with PostgreSQL database operations (JSONB support)
- Writing Python tests and improving code quality
- Refactoring Python code with design patterns
- Implementing authentication or business logic

**Key capabilities**:
- FastAPI/Flask API development
- PostgreSQL query optimization with JSONB
- Async database operations (asyncpg)
- Pandas data transformations
- Unit and integration testing with pytest
- Type hints and code documentation
- Error handling and logging best practices

**Note**: DuckDB is used only for local development and is managed by a separate local data agent.

### 2. Web Frontend Agent (`web-frontend.md`)
**Expertise**: Interactive web UI development and data visualization

**Use when**:
- Building interactive charts and visualizations
- Creating responsive web interfaces
- Implementing user interactions and animations
- Optimizing frontend performance
- Working with React, TypeScript, and Plotly.js
- Designing accessible and user-friendly interfaces

**Key capabilities**:
- React component development with TypeScript
- Plotly.js interactive chart implementation
- Responsive design and mobile optimization
- State management and API integration
- Accessibility (WCAG 2.1 AA compliance)
- Performance optimization for data-heavy UIs

## Recommended Technology Stack

Based on the repository requirements, the following technology stack is recommended:

### Backend
- **Python 3.9+** - Main language for data processing
- **FastAPI** - Modern, fast API framework with automatic OpenAPI docs
- **PostgreSQL** - Production database with JSONB support for flexible data storage
- **asyncpg** - High-performance async PostgreSQL driver
- **Alembic** - Database migration tool
- **Pandas** - Data manipulation and analysis (already in use)
- **Pytest** - Testing framework

**Note**: DuckDB is available for local development and exploratory data analysis, but is not used in production backend services.

### Frontend
- **React 18+** - Component-based UI library
- **TypeScript** - Type-safe JavaScript
- **Vite** - Fast build tool and dev server
- **Plotly.js (react-plotly.js)** - Interactive charting library (matches existing notebook usage)
- **React Query (TanStack Query)** - Data fetching and caching
- **Tailwind CSS** - Utility-first CSS framework

### Why This Stack?

1. **PostgreSQL with JSONB**: Production-grade relational database with flexible semi-structured data support via JSONB. Excellent for time-series workout data with varying metadata while maintaining ACID guarantees.

2. **Plotly.js**: Maintains consistency with existing Jupyter notebook visualizations while providing rich interactive features (zoom, pan, hover, export)

3. **React + TypeScript**: Industry-standard combination for building maintainable, type-safe web applications with excellent developer experience

4. **Vite**: Significantly faster than Create React App, with better performance and modern tooling

5. **FastAPI + asyncpg**: High-performance async Python framework with automatic API documentation and type validation, paired with the fastest PostgreSQL driver

## How to Use Custom Agents

Custom agents are invoked by GitHub Copilot when working on tasks that match their expertise. You can explicitly request an agent's help by mentioning the type of work:

```
"Help me create a FastAPI endpoint to fetch workout data"
→ Python Backend Agent will be engaged

"Build an interactive chart showing heart rate zones"
→ Web Frontend Agent will be engaged
```

## Agent Coordination

For full-stack features, both agents may be used sequentially:

1. **Python Backend Agent**: Creates API endpoints
2. **Web Frontend Agent**: Builds UI components that consume those APIs

The agents are designed to work together with compatible interfaces and conventions.

## Updating Agents

When updating agent instructions:

1. Keep instructions focused on specific domain expertise
2. Include concrete code examples
3. Reference repository-specific patterns and conventions
4. Update both agent files if changes affect integration points
5. Test that instructions lead to consistent, high-quality output

## Questions or Issues?

If you notice issues with agent behavior or have suggestions for improvement, please open an issue in the repository with the label `agent-instructions`.

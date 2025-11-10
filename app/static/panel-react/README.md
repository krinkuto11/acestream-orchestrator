# Acestream Orchestrator React Dashboard

Modern, high-performance dashboard built with React and Material-UI.

## Features

### Performance Optimizations
- **React Hooks**: Efficient state management with `useState`, `useEffect`, and `useCallback`
- **LocalStorage Caching**: Settings and preferences are cached to reduce network requests
- **Auto-refresh**: Configurable polling intervals (2s, 5s, 10s, 30s) for live status updates
- **Material-UI Components**: Modern, responsive design with excellent performance

### Visual Hierarchy
- **Material-UI Cards**: Clean, organized layout with cards for engines, streams, and VPN status
- **Typography**: Well-structured text hierarchy for clarity
- **Color-coded Status**: Intuitive health indicators (green/red/gray)
- **Icons**: Visual cues with Material-UI icons
- **Dividers and Spacing**: Clear separation of content sections

### Key Components
1. **Header**: AppBar with server configuration and refresh controls
2. **KPI Cards**: Dashboard metrics (engines, streams, health, VPN, last update)
3. **Engine List**: Cards showing engine status, health, and usage
4. **Stream List**: Active streams with details and statistics
5. **VPN Status**: VPN connection info and port forwarding
6. **Stream Detail**: Detailed view with real-time charts using Chart.js

## Development

### Prerequisites
- Node.js 20+ and npm

### Install Dependencies
```bash
npm install
```

### Development Server
```bash
npm run dev
```
This starts a development server at http://localhost:3000 with hot module replacement.

### Build for Production
```bash
npm run build
```
This builds the app and outputs to `../panel/` directory, which is served by FastAPI at `/panel`.

**Note**: When deploying via Docker, the build is performed automatically during image creation. You only need to build manually for local development.

## Architecture

### Tech Stack
- **React 18**: Modern React with hooks
- **Material-UI 5**: Comprehensive UI component library
- **Vite**: Fast build tool and dev server
- **Chart.js**: Interactive charts for stream statistics
- **React Chart.js 2**: React wrapper for Chart.js

### State Management
- Local state with `useState` for component-specific data
- Custom hooks (`useLocalStorage`) for persistent preferences
- `useCallback` for optimized function references to prevent unnecessary re-renders
- `useEffect` for data fetching and polling

### Performance Features
1. **Efficient Rendering**: Components only re-render when their props/state change
2. **Memoized Callbacks**: `useCallback` prevents function recreation on every render
3. **Conditional Rendering**: Components render only when needed
4. **Optimized Polling**: Single fetch for all data sources using `Promise.all`

## Configuration

The dashboard supports the following settings (persisted in localStorage):
- **Server URL**: Orchestrator API endpoint
- **API Key**: Bearer token for authentication
- **Refresh Interval**: Auto-refresh polling interval

## Comparison with Previous Implementation

### Previous (Vanilla JS)
- ~1000 lines of JavaScript in a single HTML file
- Manual DOM manipulation
- No component structure
- Harder to maintain and extend

### Current (React + Material-UI)
- Modular component architecture
- Declarative UI with React
- Professional Material-UI design system
- Better performance with React's virtual DOM
- Easier to maintain and extend
- Modern development workflow with hot reload

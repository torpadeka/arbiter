import React from 'react'
import ReactDOM from 'react-dom/client'

// Bundled locally rather than fetched from a CDN, so the UI renders correctly
// with no network. Oswald substitutes for Pragmatica Cond, Roboto Mono is used
// as is for the system register. Weight 400 only.
import '@fontsource/oswald/400.css'
import '@fontsource/roboto-mono/400.css'

import App from './App.jsx'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'

const SettingsFormContext = createContext(null)

export function SettingsFormProvider({ children, authRequired = false, authChecked = false }) {
  const [sections, setSections] = useState({})
  const pendingUnregistersRef = useRef(new Map())

  const registerSection = useCallback((sectionId, config) => {
    const pending = pendingUnregistersRef.current.get(sectionId)
    if (pending) {
      clearTimeout(pending)
      pendingUnregistersRef.current.delete(sectionId)
    }

    setSections((prev) => {
      const previous = prev[sectionId] || {}
      return {
        ...prev,
        [sectionId]: {
          ...previous,
          id: sectionId,
          title: config?.title || previous.title || sectionId,
          requiresAuth: Boolean(config?.requiresAuth),
          save: typeof config?.save === 'function' ? config.save : previous.save,
          discard: typeof config?.discard === 'function' ? config.discard : previous.discard,
          dirty: typeof previous.dirty === 'boolean' ? previous.dirty : false,
          saving: typeof previous.saving === 'boolean' ? previous.saving : false,
        },
      }
    })
  }, [])

  const unregisterSection = useCallback((sectionId) => {
    const pending = pendingUnregistersRef.current.get(sectionId)
    if (pending) {
      clearTimeout(pending)
    }

    const timer = setTimeout(() => {
      pendingUnregistersRef.current.delete(sectionId)
      setSections((prev) => {
        if (!prev[sectionId]) return prev
        const next = { ...prev }
        delete next[sectionId]
        return next
      })
    }, 0)

    pendingUnregistersRef.current.set(sectionId, timer)
  }, [])

  useEffect(() => {
    return () => {
      pendingUnregistersRef.current.forEach((timer) => clearTimeout(timer))
      pendingUnregistersRef.current.clear()
    }
  }, [])

  const setSectionDirty = useCallback((sectionId, dirty) => {
    setSections((prev) => {
      if (!prev[sectionId]) return prev
      return {
        ...prev,
        [sectionId]: {
          ...prev[sectionId],
          dirty: Boolean(dirty),
        },
      }
    })
  }, [])

  const setSectionSaving = useCallback((sectionId, saving) => {
    setSections((prev) => {
      if (!prev[sectionId]) return prev
      return {
        ...prev,
        [sectionId]: {
          ...prev[sectionId],
          saving: Boolean(saving),
        },
      }
    })
  }, [])

  const sectionList = useMemo(() => Object.values(sections), [sections])
  const dirtySections = useMemo(() => sectionList.filter((section) => section.dirty), [sectionList])
  const globalDirty = dirtySections.length > 0
  const globalSaving = sectionList.some((section) => section.saving)

  const value = useMemo(() => ({
    authRequired,
    authChecked,
    sectionList,
    dirtySections,
    globalDirty,
    globalSaving,
    registerSection,
    unregisterSection,
    setSectionDirty,
    setSectionSaving,
  }), [
    authRequired,
    authChecked,
    sectionList,
    dirtySections,
    globalDirty,
    globalSaving,
    registerSection,
    unregisterSection,
    setSectionDirty,
    setSectionSaving,
  ])

  return (
    <SettingsFormContext.Provider value={value}>
      {children}
    </SettingsFormContext.Provider>
  )
}

export function useSettingsForm() {
  const context = useContext(SettingsFormContext)
  if (!context) {
    throw new Error('useSettingsForm must be used within SettingsFormProvider')
  }
  return context
}

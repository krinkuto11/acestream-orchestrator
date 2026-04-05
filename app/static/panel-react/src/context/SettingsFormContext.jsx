import React, { createContext, useCallback, useContext, useMemo, useState } from 'react'

const SettingsFormContext = createContext(null)

export function SettingsFormProvider({ children, authRequired = false, authChecked = false }) {
  const [sections, setSections] = useState({})

  const registerSection = useCallback((sectionId, config) => {
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
    setSections((prev) => {
      const next = { ...prev }
      delete next[sectionId]
      return next
    })
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

import { Tabs, TabsContent } from "@/components/ui/tabs";
import { useUIStore } from "@/stores/ui";
import { Brief } from "@/views/Brief";
import { Matches } from "@/views/Matches";
import { History } from "@/views/History";
import { Settings } from "@/views/Settings";
import { Header } from "@/components/Header";

/**
 * Lantern app shell. Top-level tabs — Brief, Matches, History, Settings —
 * matching the product spec. Tab state lives in the UI Zustand store
 * so deep links / refresh / programmatic navigation can drive it
 * without prop-drilling.
 *
 * The `<Tabs>` Radix root wraps BOTH the Header and the main panel.
 * That's deliberate: the TabsList lives inside Header so the navbar
 * and the page chrome read as one cohesive surface, while the
 * TabsContent panels stay here in the body. Both pieces share the
 * Tabs context because they live under the same Root.
 */
export default function App() {
  const tab = useUIStore((s) => s.currentTab);
  const setTab = useUIStore((s) => s.setCurrentTab);

  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)} className="lantern-page">
      <Header />
      <main className="container py-6 flex-1">
        <TabsContent value="brief" className="mt-0">
          <Brief />
        </TabsContent>
        <TabsContent value="matches" className="mt-0">
          <Matches />
        </TabsContent>
        <TabsContent value="history" className="mt-0">
          <History />
        </TabsContent>
        <TabsContent value="settings" className="mt-0">
          <Settings />
        </TabsContent>
      </main>
    </Tabs>
  );
}

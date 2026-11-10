import { AppHeader } from "@/components/app-header";
import { AppNav } from "@/components/app-nav";
import { ChatPanel } from "@/components/chat/chat-panel";
import { HomeLogo } from "@/components/home-logo";
import { LogoutButton } from "@/components/logout-button";
import { OverallProvider } from "@/components/overall-state";
import { PinnedBarProvider } from "@/components/pinned-bar";
import { ThemeToggle } from "@/components/theme-toggle";
import { TimeZoneProvider } from "@/components/time-zone";

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <TimeZoneProvider>
      <OverallProvider>
        <PinnedBarProvider>
          <div className="flex min-h-dvh flex-col">
            <AppHeader>
              <div className="mx-auto flex w-full max-w-[1120px] flex-wrap items-center gap-x-4 gap-y-2 px-4 pt-4 pb-2 sm:px-6 sm:pt-5">
                <HomeLogo />
                <div className="order-last min-w-0 basis-full sm:order-none sm:basis-auto">
                  <AppNav />
                </div>
                <div className="ml-auto flex items-center gap-2">
                  <ThemeToggle />
                  <LogoutButton />
                </div>
              </div>
            </AppHeader>
            <main className="mx-auto w-full max-w-[1120px] flex-1 px-4 py-6 sm:px-6">
              {children}
            </main>
            <ChatPanel />
          </div>
        </PinnedBarProvider>
      </OverallProvider>
    </TimeZoneProvider>
  );
}

/** CASINO website content contract v1. See README.md for validation and API rules.
 * These are wire types, not database models. IDs are opaque, stable strings.
 * Dates use YYYY-MM-DD (YYYY-MM for month-only alumni dates); instants use RFC 3339 with an explicit timezone.
 */
export type ID = string;
export type DateOnly = string;
export type YearMonth = string;
export type Instant = string;
export type HttpUrl = string;
/** HTTP(S) URL or a local ./assets/... or /assets/... path, relative to the page. */
export type AssetPath = string;
export type PublishState = 'draft' | 'published' | 'archived';

export interface Content {
  id: ID;
  slug: string;
  publishState: PublishState;
  isDemo: boolean;
  createdAt: Instant;
  updatedAt: Instant;
  publishedAt: Instant | null;
  sourceUrl?: HttpUrl;
}

export interface Link {
  label: string;
  url: HttpUrl;
}

export interface Asset {
  id: ID;
  url: AssetPath;
  mimeType: 'image/jpeg' | 'image/png' | 'image/webp' | 'image/svg+xml' | 'application/pdf';
  alt: string;
  caption: string | null;
  credit: string | null;
  width: number | null;
  height: number | null;
}

export type MemberRole = 'faculty' | 'postdoc' | 'phd_student' | 'masters_student'
  | 'undergraduate' | 'research_assistant' | 'visiting_researcher';

export interface Membership {
  role: MemberRole;
  isCurrent: boolean;
  startedOn: DateOnly | null;
  endedOn: DateOnly | null;
}

export interface Person extends Content {
  name: string;
  familyName: string;
  preferredName: string | null;
  title: string | null;
  affiliation: string | null;
  portraitAssetId: ID | null;
  bioMarkdown: string;
  researchAreaIds: ID[];
  interests: string[];
  memberships: Membership[];
  state: 'member' | 'alumni';
  alumniSince: DateOnly | YearMonth | null;
  alumniDestination: string | null;
  publicEmail: string | null;
  links: Link[];
  sortOrder: number;
}

export interface ResearchArea extends Content {
  title: string;
  summary: string;
  bodyMarkdown: string;
  questions: string[];
  tags: string[];
  coverAssetId: ID | null;
  sortOrder: number;
}

export interface PublicationAuthor {
  /** Null for an external author without a lab profile. */
  personId: ID | null;
  /** Exact display name on the paper; preserve it if the profile name changes. */
  name: string;
  affiliation: string | null;
  isCorresponding: boolean | null;
  equalContribution: boolean | null;
}

export type PublicationType = 'conference' | 'journal' | 'workshop' | 'preprint'
  | 'book_chapter' | 'thesis' | 'other';

export interface Publication extends Content {
  title: string;
  /** Public paper page: HTTP(S) publisher/project URL, or #publications/{id}. */
  pageUrl: string;
  /** Array order is the authoritative author order. Never alphabetize. */
  authors: PublicationAuthor[];
  year: number;
  publicationDate: DateOnly | null;
  type: PublicationType;
  publicationStatus: 'preprint' | 'accepted' | 'published' | null;
  venue: { name: string; shortName: string | null };
  abstract: string | null;
  researchAreaIds: ID[];
  keywords: string[];
  thumbnailAssetId: ID | null;
  /** Bare DOI, e.g. 10.xxxx/xxxxx; not a doi.org URL. */
  doi: string | null;
  pdfAssetId: ID | null;
  pdfUrl: HttpUrl | null;
  projectUrl: HttpUrl | null;
  codeUrl: HttpUrl | null;
  videoUrl: HttpUrl | null;
  volume: string | null;
  issue: string | null;
  pages: string | null;
  awards: string[];
  /** Verified citation, not generated from incomplete example metadata. */
  bibtex: string | null;
  featured: boolean;
}

export interface NewsItem extends Content {
  title: string;
  category: 'lab_news' | 'publication' | 'event' | 'lab_life' | 'award';
  summary: string;
  bodyMarkdown: string;
  /** Display date of the news item, separate from the event's start time. */
  date: DateOnly;
  coverAssetId: ID | null;
  galleryAssetIds: ID[];
  relatedPersonIds: ID[];
  relatedPublicationIds: ID[];
  event: {
    startsAt: Instant;
    endsAt: Instant | null;
    timeZone: string;
    location: string;
    registrationUrl: HttpUrl | null;
  } | null;
  pinned: boolean;
}

export interface Opportunity extends Content {
  title: string;
  kind: 'phd' | 'masters' | 'research_assistant' | 'postdoc' | 'collaboration';
  availability: 'open' | 'closed';
  summary: string;
  bodyMarkdown: string;
  requirements: string[];
  applicationChecklist: string[];
  deadlineAt: Instant | null;
  contactEmail: string | null;
  applicationUrl: HttpUrl | null;
  guidanceUrl: HttpUrl | null;
  researchAreaIds: ID[];
  sortOrder: number;
}

export interface SiteSettings {
  name: string;
  fullName: string;
  institution: string;
  introduction: string;
  aboutMarkdown?: string;
  recruitmentMarkdown?: string;
  publicationArchiveUrl?: HttpUrl;
  logoAssetId: ID;
  wordmarkAssetId?: ID;
  darkLogoAssetId: ID | null;
  heroPhotoAssetIds: ID[];
  publicEmail: string | null;
  address: string | null;
  links: Link[];
  featuredPublicationIds: ID[];
  updatedAt: Instant;
}

/** Export/import envelope and local mock data. Not a public dump endpoint. */
export interface LabDataset {
  schemaVersion: '1.0';
  assets: Asset[];
  people: Person[];
  researchAreas: ResearchArea[];
  publications: Publication[];
  news: NewsItem[];
  opportunities: Opportunity[];
  siteSettings: SiteSettings;
}

export interface ListResponse<T> {
  data: T[];
  meta: { page: number; pageSize: number; total: number; totalPages: number };
}

export interface ItemResponse<T> { data: T }

export interface ApiError {
  error: {
    code: 'INVALID_QUERY' | 'NOT_FOUND' | 'UNAUTHORIZED' | 'FORBIDDEN' | 'CONFLICT' | 'VALIDATION_ERROR';
    message: string;
    fields?: Record<string, string>;
  };
}

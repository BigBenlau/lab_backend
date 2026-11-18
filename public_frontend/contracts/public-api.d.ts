// 自動產生自docs/public-openapi.json，契約7.1.0；請勿手改。
export interface ApiError {
  detail: string;
  fields?: Array<Array<string | number>> | null;
}

export interface Asset {
  id: string;
  url: string;
  metadata: AssetMetadata;
}

export interface AssetMetadata {
  mimeType: "image/svg+xml" | "image/png" | "image/jpeg" | "image/webp" | "application/pdf";
  alt: string;
  caption: string | null;
  credit: string;
  purpose: "group_photo" | "other";
}

export interface Author {
  personId: string | null;
  name: string;
}

export interface Counts {
  publications: number;
}

export interface Home {
  assets: Array<Asset>;
  recentPublications: Array<PublicationSummary>;
  counts: Counts;
}

export interface ImageMetadata {
  mimeType: "image/svg+xml" | "image/png" | "image/jpeg" | "image/webp";
  alt: string;
  caption: string | null;
  credit: string;
}

export interface Link {
  label: string;
  url: string;
}

export interface Membership {
  role: string;
  isCurrent: boolean;
}

export interface PersonDetail {
  person: PublicPerson;
  publications: Array<PublicPublication>;
}

export interface PersonSummary {
  id: string;
  familyName: string;
  givenName: string;
  englishName: string | null;
  name: string;
  title: string;
  portrait: Photo | null;
  bioMarkdown: string;
  memberships: Array<Membership>;
  alumniSince: string | null;
}

export interface Photo {
  url: string;
  metadata: ImageMetadata;
}

export interface PublicPerson {
  id: string;
  familyName: string;
  givenName: string;
  englishName: string | null;
  name: string;
  title: string;
  portrait: Photo | null;
  bioMarkdown: string;
  memberships: Array<Membership>;
  alumniSince: string | null;
  interests: Array<string>;
  publicEmail: string | null;
  links: Array<Link>;
}

export interface PublicPublication {
  id: string;
  title: string;
  year: number;
  type: "conference" | "journal" | "workshop";
  venue: Venue;
  authors: Array<Author>;
  pdfPath: string | null;
  sourceUrl: string;
}

export interface PublicationSummary {
  id: string;
  title: string;
  year: number;
  type: "conference" | "journal" | "workshop";
  venue: Venue;
}

export interface Venue {
  name: string;
  shortName: string;
}

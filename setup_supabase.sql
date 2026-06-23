-- Step 1: Create articles table
CREATE TABLE articles (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    slack_ts TEXT,
    raw_text TEXT,
    ocr_text TEXT,
    image_url TEXT,
    source_urls JSONB DEFAULT '[]',
    analysis JSONB,
    matched BOOLEAN DEFAULT false,
    category TEXT,
    headline TEXT
);

-- Step 2: Create indexes
CREATE INDEX idx_articles_created_at ON articles (created_at DESC);
CREATE INDEX idx_articles_matched ON articles (matched);
CREATE INDEX idx_articles_category ON articles (category);

-- Step 3: Create storage bucket
INSERT INTO storage.buckets (id, name, public)
VALUES ('article-images', 'article-images', true)
ON CONFLICT (id) DO NOTHING;

-- Step 4: Public read policy
CREATE POLICY "Public read access"
ON storage.objects FOR SELECT
USING (bucket_id = 'article-images');

-- Step 5: Service role upload policy
CREATE POLICY "Service role upload"
ON storage.objects FOR INSERT
WITH CHECK (bucket_id = 'article-images');

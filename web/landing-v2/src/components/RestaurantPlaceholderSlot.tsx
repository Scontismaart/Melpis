import React, { useState, useRef } from 'react';

interface RestaurantPlaceholderSlotProps {
  slotId?: string;
  slotLabel?: string;
  windowTitle: string;
  tag: string;
  title: string;
  description: string;
  format?: string;
  buttonText?: string;
  aspectRatio?: string;
  initialImage?: string;
}

export default function RestaurantPlaceholderSlot({
  slotId,
  slotLabel,
  windowTitle,
  tag,
  title,
  description,
  format = 'Formato: 16:10 (es. 1440×900px)',
  buttonText = '+ Clicca o trascina per provare un\'immagine',
  aspectRatio = '16 / 10',
  initialImage,
}: RestaurantPlaceholderSlotProps) {
  const [imageSrc, setImageSrc] = useState<string | null>(initialImage || null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      const url = URL.createObjectURL(file);
      setImageSrc(url);
    }
  };

  const handleTriggerClick = () => {
    fileInputRef.current?.click();
  };

  return (
    <div className="mv2-rest-mockup-frame" data-slot-id={slotId}>
      {/* Top Window Bar */}
      <div className="mv2-rest-mockup-topbar">
        <div className="mv2-rest-mockup-left">
          <span className="mv2-rest-window-dot" aria-hidden="true" />
          <span className="mv2-rest-window-title">{windowTitle}</span>
        </div>
        {slotLabel && (
          <div className="mv2-rest-mockup-right">
            <span className="mv2-rest-slot-badge">{slotLabel}</span>
          </div>
        )}
      </div>

      {/* Mockup Canvas */}
      <div className="mv2-rest-mockup-canvas" style={{ aspectRatio }}>
        {imageSrc ? (
          <div className="mv2-rest-mockup-preview-wrap">
            <img src={imageSrc} alt={title} className="mv2-rest-mockup-preview-img" />
            <button
              type="button"
              className="mv2-rest-mockup-reset-btn"
              onClick={() => setImageSrc(null)}
              aria-label="Rimuovi immagine e torna al blueprint"
            >
              Ripristina placeholder
            </button>
          </div>
        ) : (
          <div className="mv2-rest-blueprint-card">
            <span className="mv2-rest-blueprint-tag">{tag}</span>
            <h4 className="mv2-rest-blueprint-title">{title}</h4>
            <p className="mv2-rest-blueprint-desc">{description}</p>
            {format && <p className="mv2-rest-blueprint-format">{format}</p>}

            <button
              type="button"
              className="mv2-rest-blueprint-upload-btn"
              onClick={handleTriggerClick}
            >
              <span>{buttonText}</span>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="mv2-rest-visually-hidden"
              onChange={handleFileChange}
              aria-label={`Carica immagine per ${slotLabel || title}`}
            />
          </div>
        )}
      </div>
    </div>
  );
}

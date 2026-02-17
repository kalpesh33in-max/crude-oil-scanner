def main():
    print("Starting bot...")

    app = Application.builder().token(BOT_TOKEN).build()

    # Accept EVERYTHING including channel posts
    app.add_handler(
        MessageHandler(filters.ALL, message_handler)
    )

    if app.job_queue:
        app.job_queue.run_repeating(process_summary, interval=300, first=10)

    print("Polling started...")

    app.run_polling(
        allowed_updates=["message", "channel_post"],
        drop_pending_updates=False
    )


if __name__ == "__main__":
    main()

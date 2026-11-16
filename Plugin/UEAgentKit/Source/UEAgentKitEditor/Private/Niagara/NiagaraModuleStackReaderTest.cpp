#include "Niagara/NiagaraModuleStackReader.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
#endif

#if WITH_DEV_AUTOMATION_TESTS

namespace NiagaraStackReaderTestPrivate
{
	const TCHAR* const FixtureSystemPath = TEXT("/Game/Dev/NS_DevStack");

	UEAgentKitNiagaraStack::FReadRequest MakeRequest()
	{
		UEAgentKitNiagaraStack::FReadRequest Request;
		Request.SystemPath = FixtureSystemPath;
		Request.bIncludeInputs = true;
		return Request;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNiagaraModuleStackReaderTest,
	"UEAgentKit.Niagara.ModuleStackReaderContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNiagaraModuleStackReaderTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	using namespace NiagaraStackReaderTestPrivate;

	TSharedPtr<FJsonObject> Result;
	UEAgentKitNiagaraStack::EReadFailure Failure = UEAgentKitNiagaraStack::EReadFailure::None;
	FString FailureMessage;

	if (!UEAgentKitNiagaraStack::ReadModuleStack(MakeRequest(), Result, Failure, FailureMessage))
	{
		AddError(FString::Printf(
			TEXT("Reading '%s' failed with '%s': %s. Generate the fixture with scripts\\GenerateNiagaraStackFixture.ps1."),
			FixtureSystemPath,
			UEAgentKitNiagaraStack::FailureCode(Failure),
			*FailureMessage));
		return false;
	}

	if (!TestTrue(TEXT("result is valid"), Result.IsValid()))
	{
		return false;
	}

	double EmitterCount = 0.0;
	Result->TryGetNumberField(TEXT("emitterCount"), EmitterCount);
	TestEqual(TEXT("fixture has two emitters"), static_cast<int32>(EmitterCount), 2);

	const TArray<TSharedPtr<FJsonValue>>* Emitters = nullptr;
	if (!TestTrue(TEXT("emitters array exists"), Result->TryGetArrayField(TEXT("emitters"), Emitters) && Emitters != nullptr))
	{
		return false;
	}

	int32 TotalModules = 0;
	int32 TotalInputs = 0;
	int32 LocalInputs = 0;
	int32 RepresentableLocalInputs = 0;
	int32 LinkedInputs = 0;
	int32 DynamicInputs = 0;
	int32 OtherInputs = 0;
	TMap<FString, int32> SourceCounts;
	bool bSawLocalValue = false;
	bool bSawLinkedTarget = false;

	const TArray<TSharedPtr<FJsonValue>>* SystemStages = nullptr;
	const bool bSawSystemStage =
		Result->TryGetArrayField(TEXT("systemStages"), SystemStages) && SystemStages != nullptr && SystemStages->Num() > 0;
	TestTrue(TEXT("system stages are reported separately"), bSawSystemStage);

	for (int32 EmitterIndex = 0; EmitterIndex < Emitters->Num(); ++EmitterIndex)
	{
		const TSharedPtr<FJsonObject> EmitterObject = (*Emitters)[EmitterIndex]->AsObject();
		if (!TestTrue(*FString::Printf(TEXT("emitter %d is an object"), EmitterIndex), EmitterObject.IsValid()))
		{
			continue;
		}

		FString EmitterName;
		EmitterObject->TryGetStringField(TEXT("name"), EmitterName);
		TestFalse(*FString::Printf(TEXT("emitter %d has a name"), EmitterIndex), EmitterName.IsEmpty());

		bool bStackAvailable = false;
		EmitterObject->TryGetBoolField(TEXT("stackAvailable"), bStackAvailable);
		TestTrue(*FString::Printf(TEXT("emitter '%s' has a stack"), *EmitterName), bStackAvailable);

		double ModuleCount = 0.0;
		EmitterObject->TryGetNumberField(TEXT("moduleCount"), ModuleCount);
		TestTrue(
			*FString::Printf(TEXT("emitter '%s' reports at least three modules"), *EmitterName),
			ModuleCount >= 3.0);

		const TArray<TSharedPtr<FJsonValue>>* Stages = nullptr;
		if (!TestTrue(
			*FString::Printf(TEXT("emitter '%s' has stages"), *EmitterName),
			EmitterObject->TryGetArrayField(TEXT("stages"), Stages) && Stages != nullptr && Stages->Num() > 0))
		{
			continue;
		}

		FString StageSummary;
		for (const TSharedPtr<FJsonValue>& StageValue : *Stages)
		{
			const TSharedPtr<FJsonObject> StageObject = StageValue->AsObject();
			if (!TestTrue(TEXT("stage is an object"), StageObject.IsValid()))
			{
				continue;
			}

			FString StageName;
			StageObject->TryGetStringField(TEXT("stage"), StageName);
			TestFalse(TEXT("stage has a name"), StageName.IsEmpty());

			const TArray<TSharedPtr<FJsonValue>>* Modules = nullptr;
			if (!StageObject->TryGetArrayField(TEXT("modules"), Modules) || Modules == nullptr)
			{
				AddError(FString::Printf(TEXT("stage '%s' has no modules array"), *StageName));
				continue;
			}

			StageSummary += FString::Printf(TEXT("%s=%d "), *StageName, Modules->Num());

			int32 PreviousEngineIndex = INDEX_NONE;

			// Order must be the real stack order: 0..N-1, strictly increasing, never sorted by name.
			for (int32 ModuleIndex = 0; ModuleIndex < Modules->Num(); ++ModuleIndex)
			{
				const TSharedPtr<FJsonObject> ModuleObject = (*Modules)[ModuleIndex]->AsObject();
				if (!TestTrue(TEXT("module is an object"), ModuleObject.IsValid()))
				{
					continue;
				}
				++TotalModules;

				double Order = -1.0;
				ModuleObject->TryGetNumberField(TEXT("order"), Order);
				TestEqual(
					*FString::Printf(TEXT("stage '%s' module %d keeps stack order"), *StageName, ModuleIndex),
					static_cast<int32>(Order),
					ModuleIndex);

				// Cross-check against the engine's own module index inside the stage.
				double EngineIndex = -1.0;
				ModuleObject->TryGetNumberField(TEXT("engineModuleIndex"), EngineIndex);
				TestTrue(
					*FString::Printf(TEXT("stage '%s' module %d engine index is valid"), *StageName, ModuleIndex),
					static_cast<int32>(EngineIndex) >= 0);
				TestTrue(
					*FString::Printf(TEXT("stage '%s' module %d engine index increases with stack order"), *StageName, ModuleIndex),
					static_cast<int32>(EngineIndex) > PreviousEngineIndex);
				PreviousEngineIndex = static_cast<int32>(EngineIndex);

				FString InstanceIdentity;
				ModuleObject->TryGetStringField(TEXT("instanceIdentity"), InstanceIdentity);
				TestFalse(*FString::Printf(TEXT("stage '%s' module %d has an instance identity"), *StageName, ModuleIndex), InstanceIdentity.IsEmpty());

				const TArray<TSharedPtr<FJsonValue>>* Inputs = nullptr;
				if (!ModuleObject->TryGetArrayField(TEXT("inputs"), Inputs) || Inputs == nullptr)
				{
					continue;
				}

				for (const TSharedPtr<FJsonValue>& InputValue : *Inputs)
				{
					const TSharedPtr<FJsonObject> InputObject = InputValue->AsObject();
					if (!InputObject.IsValid())
					{
						continue;
					}
					++TotalInputs;

					FString Source;
					InputObject->TryGetStringField(TEXT("source"), Source);
					TestFalse(TEXT("input has a source"), Source.IsEmpty());
					++SourceCounts.FindOrAdd(Source);

					if (Source == TEXT("Local"))
					{
						++LocalInputs;
						bool bRepresentable = false;
						InputObject->TryGetBoolField(TEXT("valueRepresentable"), bRepresentable);
						const bool bHasValue = InputObject->TryGetField(TEXT("value")).IsValid();
						if (bRepresentable)
						{
							++RepresentableLocalInputs;
							TestTrue(TEXT("representable local input carries a value"), bHasValue);
							bSawLocalValue = bSawLocalValue || bHasValue;
						}
					}
					else if (Source == TEXT("Linked"))
					{
						++LinkedInputs;
						FString LinkedTarget;
						InputObject->TryGetStringField(TEXT("linkedTarget"), LinkedTarget);
						TestFalse(TEXT("linked input names its target"), LinkedTarget.IsEmpty());
						bSawLinkedTarget = bSawLinkedTarget || !LinkedTarget.IsEmpty();
					}
					else if (Source == TEXT("Dynamic"))
					{
						++DynamicInputs;
					}
					else
					{
						++OtherInputs;
					}
				}
			}
		}

		AddInfo(FString::Printf(
			TEXT("Niagara emitter '%s': modules=%d stages[ %s]"),
			*EmitterName,
			static_cast<int32>(ModuleCount),
			*StageSummary.TrimEnd()));
	}

	FString SourceSummary;
	for (const TPair<FString, int32>& Pair : SourceCounts)
	{
		SourceSummary += FString::Printf(TEXT("%s=%d "), *Pair.Key, Pair.Value);
	}

	AddInfo(FString::Printf(
		TEXT("Niagara stack totals: emitters=%d modules=%d inputs=%d local=%d(representable=%d) linked=%d dynamic=%d other=%d sources[ %s]"),
		static_cast<int32>(EmitterCount),
		TotalModules,
		TotalInputs,
		LocalInputs,
		RepresentableLocalInputs,
		LinkedInputs,
		DynamicInputs,
		OtherInputs,
		*SourceSummary.TrimEnd()));

	TestTrue(TEXT("at least one Local input reports a value"), bSawLocalValue);
	TestTrue(TEXT("at least one Linked input reports its target"), bSawLinkedTarget);

	// Bounded read: one module per emitter must be reported as truncated.
	UEAgentKitNiagaraStack::FReadRequest BoundedRequest = MakeRequest();
	BoundedRequest.MaxModules = 1;
	TSharedPtr<FJsonObject> BoundedResult;
	if (UEAgentKitNiagaraStack::ReadModuleStack(BoundedRequest, BoundedResult, Failure, FailureMessage))
	{
		bool bTruncated = false;
		BoundedResult->TryGetBoolField(TEXT("truncated"), bTruncated);
		TestTrue(TEXT("maxModules is enforced and reported"), bTruncated);
	}
	else
	{
		AddError(FString::Printf(TEXT("bounded read failed: %s"), *FailureMessage));
	}

	// Missing asset must map to the documented error code.
	UEAgentKitNiagaraStack::FReadRequest MissingRequest = MakeRequest();
	MissingRequest.SystemPath = TEXT("/Game/Dev/NS_DoesNotExist");
	TSharedPtr<FJsonObject> MissingResult;
	UEAgentKitNiagaraStack::EReadFailure MissingFailure = UEAgentKitNiagaraStack::EReadFailure::None;
	FString MissingMessage;
	const bool bMissingSucceeded = UEAgentKitNiagaraStack::ReadModuleStack(MissingRequest, MissingResult, MissingFailure, MissingMessage);
	TestFalse(TEXT("missing system is rejected"), bMissingSucceeded);
	TestEqual(
		TEXT("missing system uses niagara-system-not-found"),
		FString(UEAgentKitNiagaraStack::FailureCode(MissingFailure)),
		FString(TEXT("niagara-system-not-found")));

	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS

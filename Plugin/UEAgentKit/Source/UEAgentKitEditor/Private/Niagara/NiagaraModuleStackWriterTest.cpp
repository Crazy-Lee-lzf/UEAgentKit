#include "Niagara/NiagaraModuleStackReader.h"
#include "Niagara/NiagaraModuleStackWriter.h"

#include "NiagaraSystem.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

#if WITH_DEV_AUTOMATION_TESTS
#include "HAL/FileManager.h"
#include "Misc/AutomationTest.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "PackageTools.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#endif

#if WITH_DEV_AUTOMATION_TESTS

namespace NiagaraInputEditTestPrivate
{
	const TCHAR* const FixtureSystemPath = TEXT("/Game/Dev/NS_DevStack");

	struct FInputRef
	{
		FString EmitterName;
		FString EmitterHandleId;
		FString Stage;
		FString ModuleName;
		FString ModuleScriptPath;
		FString InputName;
		FString InputHandle;
		FString InputType;
		FString Source;
		FString LinkedTarget;
		TSharedPtr<FJsonValue> Value;
	};

	bool ReadStack(TSharedPtr<FJsonObject>& OutStack, FString& OutError)
	{
		UEAgentKitNiagaraStack::FReadRequest Request;
		Request.SystemPath = FixtureSystemPath;
		Request.bIncludeInputs = true;

		UEAgentKitNiagaraStack::EReadFailure Failure = UEAgentKitNiagaraStack::EReadFailure::None;
		FString Message;
		if (!UEAgentKitNiagaraStack::ReadModuleStack(Request, OutStack, Failure, Message))
		{
			OutError = FString::Printf(
				TEXT("%s: %s"), UEAgentKitNiagaraStack::FailureCode(Failure), *Message);
			return false;
		}
		return true;
	}

	void CollectInputs(const TSharedPtr<FJsonObject>& Stack, TArray<FInputRef>& OutInputs)
	{
		const TArray<TSharedPtr<FJsonValue>>* Emitters = nullptr;
		if (!Stack.IsValid() || !Stack->TryGetArrayField(TEXT("emitters"), Emitters) || Emitters == nullptr)
		{
			return;
		}

		for (const TSharedPtr<FJsonValue>& EmitterValue : *Emitters)
		{
			const TSharedPtr<FJsonObject> Emitter = EmitterValue->AsObject();
			if (!Emitter.IsValid())
			{
				continue;
			}

			FString EmitterName;
			FString HandleId;
			Emitter->TryGetStringField(TEXT("name"), EmitterName);
			Emitter->TryGetStringField(TEXT("handleId"), HandleId);

			const TArray<TSharedPtr<FJsonValue>>* Stages = nullptr;
			if (!Emitter->TryGetArrayField(TEXT("stages"), Stages) || Stages == nullptr)
			{
				continue;
			}

			for (const TSharedPtr<FJsonValue>& StageValue : *Stages)
			{
				const TSharedPtr<FJsonObject> Stage = StageValue->AsObject();
				if (!Stage.IsValid())
				{
					continue;
				}

				FString StageName;
				Stage->TryGetStringField(TEXT("stage"), StageName);

				const TArray<TSharedPtr<FJsonValue>>* Modules = nullptr;
				if (!Stage->TryGetArrayField(TEXT("modules"), Modules) || Modules == nullptr)
				{
					continue;
				}

				for (const TSharedPtr<FJsonValue>& ModuleValue : *Modules)
				{
					const TSharedPtr<FJsonObject> Module = ModuleValue->AsObject();
					if (!Module.IsValid())
					{
						continue;
					}

					FString ModuleName;
					FString ModuleScriptPath;
					Module->TryGetStringField(TEXT("name"), ModuleName);
					Module->TryGetStringField(TEXT("scriptPath"), ModuleScriptPath);

					const TArray<TSharedPtr<FJsonValue>>* Inputs = nullptr;
					if (!Module->TryGetArrayField(TEXT("inputs"), Inputs) || Inputs == nullptr)
					{
						continue;
					}

					for (const TSharedPtr<FJsonValue>& InputValue : *Inputs)
					{
						const TSharedPtr<FJsonObject> Input = InputValue->AsObject();
						if (!Input.IsValid())
						{
							continue;
						}

						FInputRef Ref;
						Ref.EmitterName = EmitterName;
						Ref.EmitterHandleId = HandleId;
						Ref.Stage = StageName;
						Ref.ModuleName = ModuleName;
						Ref.ModuleScriptPath = ModuleScriptPath;
						Input->TryGetStringField(TEXT("name"), Ref.InputName);
						Input->TryGetStringField(TEXT("handle"), Ref.InputHandle);
						Input->TryGetStringField(TEXT("type"), Ref.InputType);
						Input->TryGetStringField(TEXT("source"), Ref.Source);
						Input->TryGetStringField(TEXT("linkedTarget"), Ref.LinkedTarget);
						Ref.Value = Input->TryGetField(TEXT("value"));
						OutInputs.Add(Ref);
					}
				}
			}
		}
	}

	const FInputRef* FindFirst(
		const TArray<FInputRef>& Inputs,
		const TFunctionRef<bool(const FInputRef&)>& Predicate)
	{
		for (const FInputRef& Ref : Inputs)
		{
			if (Predicate(Ref))
			{
				return &Ref;
			}
		}
		return nullptr;
	}

	FString FindInputValue(const TArray<FInputRef>& Inputs, const FInputRef& Match)
	{
		for (const FInputRef& Ref : Inputs)
		{
			if (Ref.EmitterName == Match.EmitterName
				&& Ref.ModuleName == Match.ModuleName
				&& Ref.InputHandle == Match.InputHandle)
			{
				FString Text;
				if (Ref.Value.IsValid())
				{
					Ref.Value->TryGetString(Text);
				}
				return Text;
			}
		}
		return FString();
	}

	bool LoadFileBytes(const FString& ObjectPath, TArray<uint8>& OutBytes)
	{
		OutBytes.Reset();
		FString PackageName = ObjectPath;
		int32 DotIndex = INDEX_NONE;
		if (PackageName.FindLastChar(TEXT('.'), DotIndex))
		{
			PackageName = PackageName.Left(DotIndex);
		}
		const FString Filename = FPackageName::LongPackageNameToFilename(
			PackageName, FPackageName::GetAssetPackageExtension());
		if (!IFileManager::Get().FileExists(*Filename))
		{
			return false;
		}
		return FFileHelper::LoadFileToArray(OutBytes, *Filename);
	}

	UEAgentKitNiagaraStack::FWriteRequest MakeWriteRequest(const FInputRef& Ref)
	{
		UEAgentKitNiagaraStack::FWriteRequest Request;
		Request.SystemPath = FixtureSystemPath;
		Request.EmitterHandleId = Ref.EmitterHandleId;
		Request.Stage = Ref.Stage;
		Request.ModuleName = Ref.ModuleName;
		Request.InputName = Ref.InputHandle.IsEmpty() ? Ref.InputName : Ref.InputHandle;
		Request.bVerifyUndo = true;
		Request.CompileTimeoutSeconds = 60.0;
		return Request;
	}

	TSharedPtr<FJsonValue> MakeNumber(const double Value)
	{
		return MakeShared<FJsonValueNumber>(Value);
	}

	TSharedPtr<FJsonValue> MakeVec3(const double X, const double Y, const double Z)
	{
		const TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
		Object->SetNumberField(TEXT("x"), X);
		Object->SetNumberField(TEXT("y"), Y);
		Object->SetNumberField(TEXT("z"), Z);
		return MakeShared<FJsonValueObject>(Object);
	}

	TSharedPtr<FJsonValue> MakeColor(const double R, const double G, const double B, const double A)
	{
		const TSharedRef<FJsonObject> Object = MakeShared<FJsonObject>();
		Object->SetNumberField(TEXT("r"), R);
		Object->SetNumberField(TEXT("g"), G);
		Object->SetNumberField(TEXT("b"), B);
		Object->SetNumberField(TEXT("a"), A);
		return MakeShared<FJsonValueObject>(Object);
	}

	/** Read one component of a value that is either a bare number or a component object. */
	double NumberComponent(const TSharedPtr<FJsonValue>& Value, const TCHAR* Field)
	{
		if (!Value.IsValid())
		{
			return 0.0;
		}

		double BareNumber = 0.0;
		if (Value->TryGetNumber(BareNumber))
		{
			return BareNumber;
		}

		const TSharedPtr<FJsonObject>* Object = nullptr;
		if (!Value->TryGetObject(Object) || Object == nullptr || !Object->IsValid())
		{
			return 0.0;
		}
		double Number = 0.0;
		(*Object)->TryGetNumberField(Field, Number);
		return Number;
	}
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
	FUEAgentKitNiagaraModuleInputEditTest,
	"UEAgentKit.Niagara.ModuleInputEditContract",
	EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FUEAgentKitNiagaraModuleInputEditTest::RunTest(const FString& Parameters)
{
	(void)Parameters;
	using namespace NiagaraInputEditTestPrivate;

	// ---- discovery ---------------------------------------------------------
	TSharedPtr<FJsonObject> Stack;
	FString StackError;
	if (!ReadStack(Stack, StackError))
	{
		AddError(FString::Printf(
			TEXT("Reading '%s' failed with %s. Generate the fixture with scripts\\GenerateNiagaraStackFixture.ps1."),
			FixtureSystemPath,
			*StackError));
		return false;
	}

	TArray<FInputRef> Inputs;
	CollectInputs(Stack, Inputs);
	TestTrue(TEXT("fixture exposes module inputs"), Inputs.Num() > 0);

	const FInputRef* FloatInput = FindFirst(Inputs, [](const FInputRef& Ref)
	{
		return Ref.Source == TEXT("Local") && Ref.InputType.Contains(TEXT("NiagaraFloat")) && Ref.Value.IsValid();
	});
	const FInputRef* VecInput = FindFirst(Inputs, [](const FInputRef& Ref)
	{
		return Ref.Source == TEXT("Local")
			&& (Ref.InputType.Contains(TEXT("Vector")) || Ref.InputType.Contains(TEXT("Position")))
			&& Ref.Value.IsValid();
	});
	const FInputRef* ColorInput = FindFirst(Inputs, [](const FInputRef& Ref)
	{
		return Ref.Source == TEXT("Local") && Ref.InputType.Contains(TEXT("LinearColor")) && Ref.Value.IsValid();
	});
	const FInputRef* LinkedInput = FindFirst(Inputs, [](const FInputRef& Ref)
	{
		return Ref.Source == TEXT("Linked") && !Ref.LinkedTarget.IsEmpty();
	});

	if (FloatInput == nullptr)
	{
		AddError(TEXT("The fixture has no Local float input to edit."));
		return false;
	}

	AddInfo(FString::Printf(
		TEXT("Niagara edit targets: float='%s.%s'(%s) vec='%s.%s'(%s) color='%s.%s'(%s) linked='%s.%s'->%s"),
		FloatInput != nullptr ? *FloatInput->ModuleName : TEXT("-"),
		FloatInput != nullptr ? *FloatInput->InputHandle : TEXT("-"),
		FloatInput != nullptr ? *FloatInput->EmitterName : TEXT("-"),
		VecInput != nullptr ? *VecInput->ModuleName : TEXT("-"),
		VecInput != nullptr ? *VecInput->InputHandle : TEXT("-"),
		VecInput != nullptr ? *VecInput->InputType : TEXT("-"),
		ColorInput != nullptr ? *ColorInput->ModuleName : TEXT("-"),
		ColorInput != nullptr ? *ColorInput->InputHandle : TEXT("-"),
		ColorInput != nullptr ? *ColorInput->InputType : TEXT("-"),
		LinkedInput != nullptr ? *LinkedInput->ModuleName : TEXT("-"),
		LinkedInput != nullptr ? *LinkedInput->InputHandle : TEXT("-"),
		LinkedInput != nullptr ? *LinkedInput->LinkedTarget : TEXT("-")));
	UEAgentKitNiagaraStack::EWriteFailure Failure = UEAgentKitNiagaraStack::EWriteFailure::None;
	FString FailureMessage;
	TSharedPtr<FJsonObject> Result;

	// ---- a. same-value idempotency (must not create a transaction) ---------
	{
		UEAgentKitNiagaraStack::FWriteRequest Request = MakeWriteRequest(*FloatInput);
		Request.Value = FloatInput->Value;

		Failure = UEAgentKitNiagaraStack::EWriteFailure::None;
		if (TestTrue(
			TEXT("same-value write succeeds"),
			UEAgentKitNiagaraStack::SetModuleInput(Request, Result, Failure, FailureMessage)))
		{
			bool bWrote = true;
			bool bDirtied = true;
			double Transactions = -1.0;
			Result->TryGetBoolField(TEXT("wrote"), bWrote);
			Result->TryGetBoolField(TEXT("dirtied"), bDirtied);
			Result->TryGetNumberField(TEXT("transactionCount"), Transactions);
			TestFalse(TEXT("same value reports wrote=false"), bWrote);
			TestEqual(TEXT("same value creates no transaction"), static_cast<int32>(Transactions), 0);
			TestFalse(TEXT("same value does not dirty the package"), bDirtied);
			bool bSaved = true;
			Result->TryGetBoolField(TEXT("saved"), bSaved);
			TestFalse(TEXT("tool never saves"), bSaved);
		}
		else
		{
			AddError(FString::Printf(TEXT("same-value write failed: %s"), *FailureMessage));
		}
	}

	// ---- b. stale expected value must be refused with zero side effects ----
	{
		UEAgentKitNiagaraStack::FWriteRequest Request = MakeWriteRequest(*FloatInput);
		Request.Value = MakeNumber(1234.5);
		Request.ExpectedOldValue = MakeNumber(98765.25); // deliberately wrong

		Failure = UEAgentKitNiagaraStack::EWriteFailure::None;
		const bool bSucceeded = UEAgentKitNiagaraStack::SetModuleInput(Request, Result, Failure, FailureMessage);
		TestFalse(TEXT("stale write is refused"), bSucceeded);
		TestEqual(
			TEXT("stale write uses niagara-input-stale"),
			FString(UEAgentKitNiagaraStack::WriteFailureCode(Failure)),
			FString(TEXT("niagara-input-stale")));

		TSharedPtr<FJsonObject> After;
		FString AfterError;
		if (ReadStack(After, AfterError))
		{
			TArray<FInputRef> AfterInputs;
			CollectInputs(After, AfterInputs);
			const FInputRef* Ref = FindFirst(AfterInputs, [&FloatInput](const FInputRef& Candidate)
			{
				return Candidate.EmitterHandleId == FloatInput->EmitterHandleId
					&& Candidate.ModuleName == FloatInput->ModuleName
					&& Candidate.InputHandle == FloatInput->InputHandle;
			});
			if (TestTrue(TEXT("stale refusal keeps the input readable"), Ref != nullptr))
			{
				TestEqual(
					TEXT("stale refusal leaves the value untouched"),
					NumberComponent(Ref->Value, TEXT("x")),
					NumberComponent(FloatInput->Value, TEXT("x")));
			}
		}
	}

	// ---- c. non-Local input is refused and reports its real source ---------
	if (LinkedInput != nullptr)
	{
		TArray<uint8> ScriptBytesBefore;
		const bool bHashAvailable = LoadFileBytes(LinkedInput->ModuleScriptPath, ScriptBytesBefore);

		UEAgentKitNiagaraStack::FWriteRequest Request = MakeWriteRequest(*LinkedInput);
		Request.Value = MakeNumber(3.5);

		Failure = UEAgentKitNiagaraStack::EWriteFailure::None;
		const bool bSucceeded = UEAgentKitNiagaraStack::SetModuleInput(Request, Result, Failure, FailureMessage);
		TestFalse(TEXT("Linked input is refused"), bSucceeded);
		TestEqual(
			TEXT("Linked refusal uses niagara-input-not-local"),
			FString(UEAgentKitNiagaraStack::WriteFailureCode(Failure)),
			FString(TEXT("niagara-input-not-local")));

		if (Result.IsValid())
		{
			FString LinkedTarget;
			FString Mode;
			Result->TryGetStringField(TEXT("linkedTarget"), LinkedTarget);
			Result->TryGetStringField(TEXT("valueMode"), Mode);
			TestEqual(TEXT("refusal reports the linked source"), Mode, FString(TEXT("Linked")));
			TestEqual(TEXT("refusal reports the linked target"), LinkedTarget, LinkedInput->LinkedTarget);
		}

		if (bHashAvailable)
		{
			TArray<uint8> ScriptBytesAfter;
			const bool bStillThere = LoadFileBytes(LinkedInput->ModuleScriptPath, ScriptBytesAfter);
			TestTrue(TEXT("module script asset is still readable"), bStillThere);
			TestTrue(
				TEXT("shared module script asset is unchanged"),
				ScriptBytesAfter == ScriptBytesBefore);
		}

		TSharedPtr<FJsonObject> After;
		FString AfterError;
		if (ReadStack(After, AfterError))
		{
			TArray<FInputRef> AfterInputs;
			CollectInputs(After, AfterInputs);
			const FInputRef* Ref = FindFirst(AfterInputs, [&LinkedInput](const FInputRef& Candidate)
			{
				return Candidate.EmitterHandleId == LinkedInput->EmitterHandleId
					&& Candidate.ModuleName == LinkedInput->ModuleName
					&& Candidate.InputHandle == LinkedInput->InputHandle;
			});
			if (TestTrue(TEXT("refused Linked input is still readable"), Ref != nullptr))
			{
				TestEqual(TEXT("refused Linked input keeps its source"), Ref->Source, FString(TEXT("Linked")));
				TestEqual(TEXT("refused Linked input keeps its target"), Ref->LinkedTarget, LinkedInput->LinkedTarget);
			}
		}
	}
	else
	{
		AddWarning(TEXT("The fixture has no Linked input; the Linked refusal branch was not exercised."));
	}

	// ---- d. a real float write: one transaction, undo/redo proven ----------
	const double FloatBefore = NumberComponent(FloatInput->Value, TEXT("x"));
	double FloatWritten = 0.0;
	{
		UEAgentKitNiagaraStack::FWriteRequest Request = MakeWriteRequest(*FloatInput);
		FloatWritten = FloatBefore + 1.0;
		Request.Value = MakeNumber(FloatWritten);

		Failure = UEAgentKitNiagaraStack::EWriteFailure::None;
		if (TestTrue(
			TEXT("float write succeeds"),
			UEAgentKitNiagaraStack::SetModuleInput(Request, Result, Failure, FailureMessage)))
		{
			bool bWrote = false;
			bool bUndoVerified = false;
			bool bRedoVerified = false;
			bool bSaved = true;
			bool bRequiresSave = false;
			double Transactions = 0.0;
			Result->TryGetBoolField(TEXT("wrote"), bWrote);
			Result->TryGetBoolField(TEXT("undoVerified"), bUndoVerified);
			Result->TryGetBoolField(TEXT("redoVerified"), bRedoVerified);
			Result->TryGetBoolField(TEXT("saved"), bSaved);
			Result->TryGetBoolField(TEXT("requiresSave"), bRequiresSave);
			Result->TryGetNumberField(TEXT("transactionCount"), Transactions);

			TestTrue(TEXT("float write reports wrote=true"), bWrote);
			TestEqual(TEXT("float write is exactly one undo step"), static_cast<int32>(Transactions), 1);
			TestTrue(TEXT("float write is undoable"), bUndoVerified);
			TestTrue(TEXT("float write is redoable"), bRedoVerified);
			AddInfo(FString::Printf(
				TEXT("Niagara float write: '%s.%s' %g -> %g, transactions=%d undo=%d redo=%d"), 
				*FloatInput->ModuleName, *FloatInput->InputHandle, FloatBefore, FloatWritten,
				static_cast<int32>(Transactions), bUndoVerified ? 1 : 0, bRedoVerified ? 1 : 0));
			TestFalse(TEXT("float write is not saved automatically"), bSaved);
			TestTrue(TEXT("float write requires an explicit save"), bRequiresSave);

			double NewValue = 0.0;
			Result->TryGetNumberField(TEXT("newValue"), NewValue);
			TestEqual(TEXT("result reports the new value"), NewValue, FloatWritten);
		}
		else
		{
			AddError(FString::Printf(TEXT("float write failed: %s"), *FailureMessage));
		}
	}

	// ---- e. vector write ----------------------------------------------------
	double VecWritten[3] = { 0.0, 0.0, 0.0 };
	if (VecInput != nullptr)
	{
		const double X = NumberComponent(VecInput->Value, TEXT("x")) + 1.0;
		const double Y = NumberComponent(VecInput->Value, TEXT("y")) - 2.0;
		const double Z = NumberComponent(VecInput->Value, TEXT("z")) + 3.0;
		VecWritten[0] = X;
		VecWritten[1] = Y;
		VecWritten[2] = Z;

		UEAgentKitNiagaraStack::FWriteRequest Request = MakeWriteRequest(*VecInput);
		Request.Value = MakeVec3(X, Y, Z);

		Failure = UEAgentKitNiagaraStack::EWriteFailure::None;
		TestTrue(
			TEXT("vector write succeeds"),
			UEAgentKitNiagaraStack::SetModuleInput(Request, Result, Failure, FailureMessage));
	}
	else
	{
		AddWarning(TEXT("The fixture has no Local vector input."));
	}

	// ---- f. color write -----------------------------------------------------
	double ColorWritten[4] = { 0.0, 0.0, 0.0, 0.0 };
	if (ColorInput != nullptr)
	{
		const double CurrentR = NumberComponent(ColorInput->Value, TEXT("r"));
		const bool bFirst = CurrentR != 0.25;
		ColorWritten[0] = bFirst ? 0.25 : 0.5;
		ColorWritten[1] = bFirst ? 0.5 : 0.25;
		ColorWritten[2] = bFirst ? 0.75 : 0.125;
		ColorWritten[3] = 1.0;

		UEAgentKitNiagaraStack::FWriteRequest Request = MakeWriteRequest(*ColorInput);
		Request.Value = MakeColor(ColorWritten[0], ColorWritten[1], ColorWritten[2], ColorWritten[3]);

		Failure = UEAgentKitNiagaraStack::EWriteFailure::None;
		TestTrue(
			TEXT("color write succeeds"),
			UEAgentKitNiagaraStack::SetModuleInput(Request, Result, Failure, FailureMessage));
	}
	else
	{
		AddWarning(TEXT("The fixture has no Local color input."));
	}

	// ---- g. read everything back -------------------------------------------
	{
		TSharedPtr<FJsonObject> After;
		FString AfterError;
		if (!TestTrue(TEXT("stack can be read after the writes"), ReadStack(After, AfterError)))
		{
			AddError(AfterError);
			return false;
		}

		TArray<FInputRef> AfterInputs;
		CollectInputs(After, AfterInputs);

		const FInputRef* FloatAfter = FindFirst(AfterInputs, [&FloatInput](const FInputRef& Candidate)
		{
			return Candidate.EmitterHandleId == FloatInput->EmitterHandleId
				&& Candidate.ModuleName == FloatInput->ModuleName
				&& Candidate.InputHandle == FloatInput->InputHandle;
		});
		if (TestTrue(TEXT("float input is readable after the write"), FloatAfter != nullptr))
		{
			TestEqual(
				TEXT("float value reads back"),
				NumberComponent(FloatAfter->Value, TEXT("x")),
				FloatWritten);

			// The other emitter must not be affected by an edit on this one.
			for (const FInputRef& Candidate : AfterInputs)
			{
				if (Candidate.EmitterHandleId != FloatInput->EmitterHandleId
					&& Candidate.ModuleName == FloatInput->ModuleName
					&& Candidate.InputHandle == FloatInput->InputHandle)
				{
					const double OtherValue = NumberComponent(Candidate.Value, TEXT("x"));
					TestNotEqual(
						TEXT("the same module input in the other emitter keeps its own value"),
						OtherValue,
						FloatWritten);
					break;
				}
			}
		}

		if (VecInput != nullptr)
		{
			const FInputRef* VecAfter = FindFirst(AfterInputs, [&VecInput](const FInputRef& Candidate)
			{
				return Candidate.EmitterHandleId == VecInput->EmitterHandleId
					&& Candidate.ModuleName == VecInput->ModuleName
					&& Candidate.InputHandle == VecInput->InputHandle;
			});
			if (TestTrue(TEXT("vector input is readable after the write"), VecAfter != nullptr))
			{
				TestEqual(TEXT("vector x reads back"), NumberComponent(VecAfter->Value, TEXT("x")), VecWritten[0]);
				TestEqual(TEXT("vector y reads back"), NumberComponent(VecAfter->Value, TEXT("y")), VecWritten[1]);
				TestEqual(TEXT("vector z reads back"), NumberComponent(VecAfter->Value, TEXT("z")), VecWritten[2]);
			}
		}

		if (ColorInput != nullptr)
		{
			const FInputRef* ColorAfter = FindFirst(AfterInputs, [&ColorInput](const FInputRef& Candidate)
			{
				return Candidate.EmitterHandleId == ColorInput->EmitterHandleId
					&& Candidate.ModuleName == ColorInput->ModuleName
					&& Candidate.InputHandle == ColorInput->InputHandle;
			});
			if (TestTrue(TEXT("color input is readable after the write"), ColorAfter != nullptr))
			{
				TestEqual(TEXT("color r reads back"), NumberComponent(ColorAfter->Value, TEXT("r")), ColorWritten[0]);
				TestEqual(TEXT("color g reads back"), NumberComponent(ColorAfter->Value, TEXT("g")), ColorWritten[1]);
				TestEqual(TEXT("color b reads back"), NumberComponent(ColorAfter->Value, TEXT("b")), ColorWritten[2]);
				TestEqual(TEXT("color a reads back"), NumberComponent(ColorAfter->Value, TEXT("a")), ColorWritten[3]);
			}
		}
	}

	// ---- h. explicit save + reload keeps the value -------------------------
	{
		const FString SystemObjectPath = FString(FixtureSystemPath) + TEXT(".") + FPackageName::GetShortName(FixtureSystemPath);
		UNiagaraSystem* System = Cast<UNiagaraSystem>(FSoftObjectPath(SystemObjectPath).TryLoad());
		if (!TestTrue(TEXT("system is loaded before the save"), System != nullptr))
		{
			return false;
		}

		UPackage* Package = System->GetOutermost();
		const FString PackageName = Package->GetName();
		const FString Filename = FPackageName::LongPackageNameToFilename(
			PackageName, FPackageName::GetAssetPackageExtension());

		FSavePackageArgs SaveArgs;
		SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
		SaveArgs.SaveFlags = SAVE_NoError;
		SaveArgs.Error = GError;
		const bool bSaved = UPackage::SavePackage(Package, System, *Filename, SaveArgs);
		if (!TestTrue(TEXT("explicit save succeeds"), bSaved))
		{
			return false;
		}

		System = nullptr;
		TArray<UPackage*> ToUnload;
		ToUnload.Add(Package);
		FText UnloadError;
		const bool bUnloaded = UPackageTools::UnloadPackages(ToUnload, UnloadError);
		TestTrue(TEXT("package unloads for the reload check"), bUnloaded);
		if (!bUnloaded)
		{
			AddError(UnloadError.ToString());
			return false;
		}

		TSharedPtr<FJsonObject> ReloadedStack;
		FString ReloadedError;
		if (!TestTrue(TEXT("stack reloads from disk"), ReadStack(ReloadedStack, ReloadedError)))
		{
			AddError(ReloadedError);
			return false;
		}

		TArray<FInputRef> ReloadedInputs;
		CollectInputs(ReloadedStack, ReloadedInputs);
		const FInputRef* ReloadedFloat = FindFirst(ReloadedInputs, [&FloatInput](const FInputRef& Candidate)
		{
			return Candidate.EmitterHandleId == FloatInput->EmitterHandleId
				&& Candidate.ModuleName == FloatInput->ModuleName
				&& Candidate.InputHandle == FloatInput->InputHandle;
		});
		if (TestTrue(TEXT("float input survives the reload"), ReloadedFloat != nullptr))
		{
			TestEqual(
				TEXT("the written value persists across an explicit save and reload"),
				NumberComponent(ReloadedFloat->Value, TEXT("x")),
				FloatWritten);
		}
	}

	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
